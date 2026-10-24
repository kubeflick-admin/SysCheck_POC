"""Thermal stress test.

Drives every logical core with back-to-back NumPy matrix multiplications
(BLAS releases the GIL, so Python threads genuinely saturate multiple
cores) while sampling CPU temperature and frequency. We flag:

* peak temperature >= THERMAL_CRITICAL_C (hard fail), or
* a sustained clock >= THERMAL_THROTTLE_DROP_MHZ below the CPU's base
  clock at the end of the run (thermal throttling). Falling from the
  turbo peak back to base is normal boost behaviour, not throttling.

Temperature source is ``MSAcpi_ThermalZoneTemperature`` (root\\wmi), which
reports tenths of a Kelvin. Many consumer laptops do not expose a usable
ACPI thermal zone to userspace (or require elevation), and some expose a
placeholder that never moves; in both cases we report
``temp_available=False`` and fall back to throttle detection via clock
behaviour alone.
"""

from __future__ import annotations

import statistics
import threading
import time
from typing import Callable, Dict, List, Optional

import numpy as np
import psutil

from .. import config
from .wmi_util import wmi_connection

LogFn = Callable[[str], None]
SampleFn = Callable[[float, Optional[float], Optional[float], float], None]
StopFn = Callable[[], bool]


def _noop(_: str) -> None:
    pass


def _kelvin_tenths_to_c(value: float) -> float:
    return round(value / 10.0 - 273.15, 1)


def _read_temp_c(conn) -> Optional[float]:
    """Return the hottest ACPI thermal zone in °C, or None if unavailable."""
    if conn is None:
        return None
    try:
        temps = []
        for zone in conn.MSAcpi_ThermalZoneTemperature():
            if zone.CurrentTemperature:
                temps.append(_kelvin_tenths_to_c(float(zone.CurrentTemperature)))
        return max(temps) if temps else None
    except Exception:  # noqa: BLE001
        return None


def _worker_load(stop_event: threading.Event) -> None:
    """Hammer a core with matrix multiplication until told to stop."""
    a = np.random.rand(256, 256)
    b = np.random.rand(256, 256)
    while not stop_event.is_set():
        # The result is discarded; we only want the FLOPs.
        a = (a @ b) * 0.5 + 0.5
        np.clip(a, 0.0, 1.0, out=a)


def _evaluate(temps: List[float], freqs: List[float],
              base_freq: Optional[float]) -> Dict[str, object]:
    """Turn the samples of a stress run into the result fields."""
    # A sensor that doesn't move under a full-load run isn't reporting the
    # CPU (some ACPI thermal zones are fixed placeholders).
    temp_stuck = bool(temps) and \
        (max(temps) - min(temps)) < config.THERMAL_STUCK_DELTA_C
    temp_available = bool(temps) and not temp_stuck
    peak_temp = max(temps) if temp_available else None

    # Throttling = the clock the CPU *holds* at the end of the run sits well
    # below its base clock; the median ignores one-off dips.
    window = max(1, int(config.THERMAL_SUSTAIN_WINDOW_S
                        * config.THERMAL_SAMPLE_HZ))
    sustained = statistics.median(freqs[-window:]) if freqs else None
    throttled = bool(base_freq and sustained and
                     base_freq - sustained >= config.THERMAL_THROTTLE_DROP_MHZ)

    return {
        "temp_available": temp_available,
        "temp_stuck": temp_stuck,
        "peak_temp_c": peak_temp,
        "base_freq_mhz": round(base_freq) if base_freq else None,
        "peak_freq_mhz": round(max(freqs)) if freqs else None,
        "sustained_freq_mhz": round(sustained) if sustained else None,
        "throttled": throttled,
        "overheated": bool(peak_temp is not None
                           and peak_temp >= config.THERMAL_CRITICAL_C),
        "samples": len(temps),
    }


def run_thermal_stress(
    log: LogFn | None = None,
    on_sample: SampleFn | None = None,
    should_stop: StopFn | None = None,
) -> Dict[str, object]:
    """Run the synthetic load and monitor sensors.

    ``on_sample(elapsed_s, temp_c, freq_mhz, cpu_pct)`` is called at
    ~THERMAL_SAMPLE_HZ for live plotting. ``should_stop()`` lets the UI
    cancel early; the result then carries ``completed=False`` and is not
    scored.
    """
    log = log or _noop
    on_sample = on_sample or (lambda *_: None)
    should_stop = should_stop or (lambda: False)

    n_threads = psutil.cpu_count(logical=True) or 1
    log(f"[THERM] Spawning {n_threads} compute thread(s) for synthetic load ...")

    stop_event = threading.Event()
    workers = [
        threading.Thread(target=_worker_load, args=(stop_event,), daemon=True)
        for _ in range(n_threads)
    ]

    temps: List[float] = []
    freqs: List[float] = []
    idle_freq = base_freq = None
    completed = False

    with wmi_connection("root\\wmi") as conn:
        temp_available = _read_temp_c(conn) is not None
        if not temp_available:
            log("[THERM] WARNING: no readable ACPI thermal zone "
                "(throttle detection will rely on clock behaviour).")

        # Baseline clocks before load. On Windows psutil's ``max`` is the
        # nominal (base) clock, not the turbo ceiling.
        try:
            freq_info = psutil.cpu_freq()
            idle_freq = freq_info.current or None
            base_freq = freq_info.max or idle_freq
        except Exception:  # noqa: BLE001
            idle_freq = base_freq = None

        for w in workers:
            w.start()
        log("[THERM] Load engaged. Monitoring sensors ...")

        start = time.monotonic()
        interval = 1.0 / config.THERMAL_SAMPLE_HZ
        duration = config.THERMAL_DURATION_S

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                completed = True
                break
            if should_stop():
                break

            temp = _read_temp_c(conn)
            try:
                freq = psutil.cpu_freq().current or None
            except Exception:  # noqa: BLE001
                freq = None
            cpu_pct = psutil.cpu_percent(interval=None)

            if temp is not None:
                temps.append(temp)
            if freq is not None:
                freqs.append(freq)

            on_sample(round(elapsed, 2), temp, freq, cpu_pct)
            if temp is not None:
                log(f"[THERM] t={elapsed:5.1f}s  temp={temp:>5.1f}C  "
                    f"clock={freq or 0:.0f}MHz  load={cpu_pct:.0f}%")
            time.sleep(interval)

        stop_event.set()

    for w in workers:
        w.join(timeout=2.0)
    log("[THERM] Load released. Evaluating results ...")

    result = _evaluate(temps, freqs, base_freq)
    result["idle_freq_mhz"] = round(idle_freq) if idle_freq else None
    result["completed"] = completed

    if not completed:
        log("[THERM]   -> stopped early — result not scored.")
    if result["temp_stuck"]:
        log("[THERM]   -> sensor reading never changed under load — "
            "treating it as unavailable.")
    if result["peak_temp_c"] is not None:
        log(f"[THERM]   -> peak temp {result['peak_temp_c']} C "
            f"(critical >= {config.THERMAL_CRITICAL_C} C)")
    if result["throttled"]:
        log(f"[THERM]   -> clock held {result['sustained_freq_mhz']} MHz vs "
            f"{result['base_freq_mhz']} MHz base — possible throttling")
    return result
