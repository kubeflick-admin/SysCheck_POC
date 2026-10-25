"""Battery health collector.

Two independent sources are tried and merged for the best coverage:

1. WMI ``root\\wmi`` ACPI battery classes
       BatteryStaticData        -> DesignedCapacity (mWh)
       BatteryFullChargedCapacity -> FullChargedCapacity (mWh)
       BatteryCycleCount        -> CycleCount
2. ``powercfg /batteryreport /xml`` as a fallback for the capacities when
   the ACPI classes are not exposed by the firmware.

Wear level is derived: ``1 - full_charge / design``. Desktops (no battery)
return ``present=False`` and are excluded from scoring.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional

import psutil

from .. import config
from .wmi_util import wmi_connection

LogFn = Callable[[str], None]
# on_sample(phase, percent, cpu_pct, elapsed_s)  — phase in {"wait", "drain"}
DischargeSampleFn = Callable[[str, Optional[int], float, float], None]
StopFn = Callable[[], bool]


def _noop(_: str) -> None:
    pass


def _wmi_battery(log: LogFn) -> Dict[str, Optional[int]]:
    data: Dict[str, Optional[int]] = {
        "design_mwh": None, "full_mwh": None, "cycles": None,
    }
    with wmi_connection("root\\wmi") as c:
        if c is None:
            return data
        try:
            for s in c.BatteryStaticData():
                if s.DesignedCapacity:
                    data["design_mwh"] = int(s.DesignedCapacity)
                break
        except Exception as exc:  # noqa: BLE001
            log(f"[BATT] BatteryStaticData n/a: {exc}")
        try:
            for f in c.BatteryFullChargedCapacity():
                if f.FullChargedCapacity:
                    data["full_mwh"] = int(f.FullChargedCapacity)
                break
        except Exception as exc:  # noqa: BLE001
            log(f"[BATT] BatteryFullChargedCapacity n/a: {exc}")
        try:
            for cc in c.BatteryCycleCount():
                if cc.CycleCount:
                    data["cycles"] = int(cc.CycleCount)
                break
        except Exception as exc:  # noqa: BLE001
            log(f"[BATT] BatteryCycleCount n/a: {exc}")
    return data


def _local(tag: str) -> str:
    """Strip the XML namespace from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _powercfg_report(log: LogFn) -> Dict[str, Optional[int]]:
    data: Dict[str, Optional[int]] = {
        "design_mwh": None, "full_mwh": None, "cycles": None,
    }
    path = os.path.join(tempfile.gettempdir(), "syscheck_battery.xml")
    try:
        subprocess.run(
            ["powercfg", "/batteryreport", "/xml", "/output", path],
            capture_output=True, timeout=30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[BATT] powercfg failed: {exc}")
        return data

    if not os.path.exists(path):
        return data

    try:
        tree = ET.parse(path)
        for batt in tree.iter():
            if _local(batt.tag) != "Battery":
                continue
            for child in batt:
                name, text = _local(child.tag), (child.text or "").strip()
                if not text.isdigit():
                    continue
                if name == "DesignCapacity":
                    data["design_mwh"] = int(text)
                elif name == "FullChargeCapacity":
                    data["full_mwh"] = int(text)
                elif name == "CycleCount":
                    data["cycles"] = int(text)
            break  # first battery only
    except Exception as exc:  # noqa: BLE001
        log(f"[BATT] parsing battery report failed: {exc}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return data


def collect_battery_health(log: LogFn | None = None) -> Dict[str, object]:
    log = log or _noop

    log("[BATT] Interrogating ACPI battery controller ...")
    data = _wmi_battery(log)

    # Fill any gaps from the powercfg battery report.
    if data["design_mwh"] is None or data["full_mwh"] is None:
        log("[BATT] Falling back to powercfg battery report ...")
        report = _powercfg_report(log)
        for k, v in report.items():
            if data[k] is None:
                data[k] = v

    design, full, cycles = data["design_mwh"], data["full_mwh"], data["cycles"]
    capacity_known = bool(design) and bool(full)
    # The OS can see a battery whose firmware won't report capacities: that
    # is a battery we couldn't assess, not a desktop.
    present = capacity_known or _battery_reading() is not None

    wear_pct = None
    if capacity_known:
        wear_pct = round(max(0.0, (1 - full / design)) * 100, 1)

    if not present:
        log("[BATT]   -> No battery detected (desktop?) — excluded from score.")
    elif not capacity_known:
        log("[BATT]   -> Battery detected, but the firmware did not report "
            "its capacity (wear unknown).")
    else:
        log(f"[BATT]   -> design={design} mWh, full-charge={full} mWh, "
            f"wear={wear_pct}%, cycles={cycles if cycles is not None else 'n/a'}")

    return {
        "present": present,
        "design_mwh": design,
        "full_mwh": full,
        "wear_pct": wear_pct,
        "cycles": cycles,
    }


# ─────────────────────────────────────────────────────────────────────
# Discharge test
# ─────────────────────────────────────────────────────────────────────
def _battery_reading():
    """psutil battery snapshot, or ``None`` if there is no battery."""
    try:
        return psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None


def _secs_left(batt) -> Optional[int]:
    """psutil ``secsleft`` as a positive int, or None when unknown/plugged."""
    if batt is None:
        return None
    secs = batt.secsleft
    # psutil uses the sentinels POWER_TIME_UNKNOWN (-1) / UNLIMITED (-2).
    if isinstance(secs, int) and secs >= 0:
        return secs
    return None


def _discharge_rate_mw(conn) -> Optional[int]:
    """Total draw (mW) reported by ACPI ``BatteryStatus``, or None.

    Firmware reports 0 while charging and for a few seconds after the
    charger is pulled, so only positive readings taken while discharging
    count. Laptops with two packs report one instance each.
    """
    if conn is None:
        return None
    total = 0
    try:
        for s in conn.BatteryStatus():
            rate = int(s.DischargeRate or 0)
            if s.Discharging and rate > 0:
                total += rate
    except Exception:  # noqa: BLE001
        return None
    return total or None


def run_discharge_test(
    full_mwh: Optional[int] = None,
    log: LogFn | None = None,
    on_sample: DischargeSampleFn | None = None,
    should_stop: StopFn | None = None,
) -> Dict[str, object]:
    """Measure the battery's power draw with the charger unplugged.

    The laptop must be running **off the battery**: while AC is still
    connected we sit in a ``wait`` phase (the UI prompts the operator to
    pull the power cord) until ``power_plugged`` goes false. The draw then
    comes from the first of these that succeeds (``result["method"]``):

    1. ``rate`` — average the firmware's own ``BatteryStatus.DischargeRate``
       over ``BATTERY_RATE_WINDOW_S``. Direct, and needs no percent tick.
    2. ``percent`` — time a whole ``BATTERY_DISCHARGE_DROP_PCT`` step.
       Windows reports whole percents and at unplug time the charge sits
       somewhere *inside* the current percent, so timing from the unplug
       would catch a fraction of a percent and overstate the draw. The
       clock therefore starts at the first observed drop and stops at the
       next one.
    3. ``estimate`` — if neither completes by
       ``BATTERY_DISCHARGE_TIMEOUT_S``, fall back to psutil's ``secsleft``.

    CPU load is averaged across the window so the draw can be read in the
    context of the load behind it. ``on_sample(phase, percent, cpu_pct,
    elapsed_s)`` feeds the live UI; ``should_stop()`` lets the operator
    cancel.
    """
    log = log or _noop
    on_sample = on_sample or (lambda *_: None)
    should_stop = should_stop or (lambda: False)

    result: Dict[str, object] = {
        "ran": False,
        "measured": False,       # True => firmware rate or a full step timed
        "estimated": False,      # True => value came from psutil secsleft
        "method": None,          # "rate" | "percent" | "estimate"
        "drop_pct": config.BATTERY_DISCHARGE_DROP_PCT,
        "seconds_per_pct": None,
        "avg_cpu_pct": None,
        "drain_w": None,
        "start_pct": None,
        "end_pct": None,
        "reason": None,
    }

    batt = _battery_reading()
    if batt is None:
        log("[DRAIN] No battery sensor — discharge test skipped.")
        result["reason"] = "No battery present."
        return result

    # ── Phase 1: wait for the operator to unplug AC ──────────────────
    interval = 1.0 / max(1, config.BATTERY_DISCHARGE_SAMPLE_HZ)
    if batt.power_plugged:
        log("[DRAIN] AC connected — waiting for the power cord to be removed ...")
    while batt is not None and batt.power_plugged:
        if should_stop():
            result["reason"] = "Cancelled before unplugging."
            return result
        pct = int(batt.percent) if batt.percent is not None else None
        on_sample("wait", pct, psutil.cpu_percent(interval=None), 0.0)
        time.sleep(interval)
        batt = _battery_reading()

    if batt is None:
        result["reason"] = "Battery sensor disappeared."
        return result

    # ── Phase 2: measure the draw ────────────────────────────────────
    result["ran"] = True
    start_pct = int(batt.percent)
    result["start_pct"] = start_pct
    log(f"[DRAIN] Running on battery at {start_pct}% — measuring the draw "
        f"(giving up after {config.BATTERY_DISCHARGE_TIMEOUT_S}s).")

    rate_samples_needed = max(1, int(config.BATTERY_RATE_WINDOW_S
                                     * config.BATTERY_DISCHARGE_SAMPLE_HZ))
    start = time.monotonic()
    cpu_samples: List[float] = []
    rates: List[int] = []
    step_start: Optional[float] = None   # elapsed time of the first drop
    step_pct: Optional[int] = None
    last_secs_left = _secs_left(batt)
    cur_pct = start_pct

    with wmi_connection("root\\wmi") as conn:
        while True:
            elapsed = time.monotonic() - start
            cur_pct = int(batt.percent)

            # AC came back mid-test: the measurement is void.
            if batt.power_plugged:
                log("[DRAIN] AC reconnected mid-test — discharge reading voided.")
                result["reason"] = "Power was reconnected during the test."
                result["end_pct"] = cur_pct
                return result

            cpu = psutil.cpu_percent(interval=None)
            cpu_samples.append(cpu)
            on_sample("drain", cur_pct, cpu, round(elapsed, 1))
            out_of_time = (elapsed >= config.BATTERY_DISCHARGE_TIMEOUT_S
                           or should_stop())

            rate = _discharge_rate_mw(conn)
            if rate:
                rates.append(rate)
            if len(rates) >= rate_samples_needed or (out_of_time and rates):
                avg_mw = sum(rates) / len(rates)
                result["measured"] = True
                result["method"] = "rate"
                result["drain_w"] = round(avg_mw / 1000, 1)
                if full_mwh:
                    # 1% of the pack is full_mwh/100 mWh, drawn at avg_mw.
                    result["seconds_per_pct"] = round(full_mwh * 36 / avg_mw, 1)
                log(f"[DRAIN] Battery reports {avg_mw / 1000:.1f} W average "
                    f"draw over {len(rates)} sample(s).")
                break

            if step_start is None:
                if cur_pct < start_pct:
                    step_start, step_pct = elapsed, cur_pct
                    log(f"[DRAIN] First drop to {cur_pct}% — timing the next "
                        f"full {config.BATTERY_DISCHARGE_DROP_PCT}% ...")
            elif cur_pct <= step_pct - config.BATTERY_DISCHARGE_DROP_PCT:
                result["measured"] = True
                result["method"] = "percent"
                result["seconds_per_pct"] = round(
                    (elapsed - step_start) / (step_pct - cur_pct), 1)
                log(f"[DRAIN] {step_pct}% -> {cur_pct}% took "
                    f"{elapsed - step_start:.0f}s.")
                break

            if out_of_time:
                # No full step in time — integer percent gives us nothing to
                # interpolate, so fall back to psutil's estimate.
                sl = _secs_left(batt) or last_secs_left
                if sl and cur_pct > 0:
                    result["estimated"] = True
                    result["method"] = "estimate"
                    result["seconds_per_pct"] = round(sl / cur_pct, 1)
                    log(f"[DRAIN] No full 1% step in {elapsed:.0f}s; using "
                        f"system estimate "
                        f"(~{result['seconds_per_pct']:.0f}s/1%).")
                else:
                    result["reason"] = ("Battery drained too slowly to measure, "
                                        "and no system estimate was available.")
                    log("[DRAIN] Could not measure or estimate discharge rate.")
                break

            last_secs_left = _secs_left(batt) or last_secs_left
            time.sleep(interval)
            batt = _battery_reading()
            if batt is None:
                result["reason"] = "Battery sensor disappeared mid-test."
                return result

    result["end_pct"] = cur_pct
    if cpu_samples:
        result["avg_cpu_pct"] = round(sum(cpu_samples) / len(cpu_samples), 1)

    # Derived average power draw, if we know the pack's full-charge energy.
    spp = result["seconds_per_pct"]
    if result["drain_w"] is None and full_mwh and spp:
        # 1% of the pack is full_mwh/100 mWh, delivered over `spp` seconds.
        #   P(W) = energy(mWh) / time(h) / 1000 = full_mwh * 0.036 / spp
        result["drain_w"] = round(full_mwh * 0.036 / spp, 1)

    if result["avg_cpu_pct"] is not None and result["method"]:
        drain = (f"{result['drain_w']} W" if result["drain_w"]
                 else "n/a (capacity unknown)")
        per_pct = f"{spp:.0f}s per 1%, " if spp else ""
        log(f"[DRAIN]   -> {per_pct}~{drain} at {result['avg_cpu_pct']:.0f}% "
            f"avg CPU load ({result['method']})")
    return result
