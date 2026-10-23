"""Thermal stress test.

Drives every logical core with back-to-back NumPy matrix multiplications
(BLAS releases the GIL, so Python threads genuinely saturate multiple
cores) while sampling CPU temperature and frequency. We flag:

* peak temperature >= THERMAL_CRITICAL_C (hard fail), or
* a sustained clock drop >= THERMAL_THROTTLE_DROP_MHZ under load
  (thermal throttling).

Temperature source is ``MSAcpi_ThermalZoneTemperature`` (root\\wmi), which
reports tenths of a Kelvin. Many consumer laptops do not expose a usable
ACPI thermal zone to userspace (or require elevation); in that case we
report ``temp_available=False`` and fall back to throttle detection via
clock behaviour alone.
"""

from __future__ import annotations

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


def run_thermal_stress(
    log: LogFn | None = None,
    on_sample: SampleFn | None = None,
    should_stop: StopFn | None = None,
) -> Dict[str, object]:
    """Run the synthetic load and monitor sensors.

    ``on_sample(elapsed_s, temp_c, freq_mhz, cpu_pct)`` is called at
    ~THERMAL_SAMPLE_HZ for live plotting. ``should_stop()`` lets the UI
    cancel early.
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
    idle_freq = None

    with wmi_connection("root\\wmi") as conn:
        temp_available = _read_temp_c(conn) is not None
        if not temp_available:
            log("[THERM] WARNING: no readable ACPI thermal zone "
                "(throttle detection will rely on clock behaviour).")

        # Baseline idle clock before load.
        try:
            idle_freq = (psutil.cpu_freq().current or None)
        except Exception:  # noqa: BLE001
            idle_freq = None

        for w in workers:
            w.start()
        log("[THERM] Load engaged. Monitoring sensors ...")

        start = time.monotonic()
        interval = 1.0 / config.THERMAL_SAMPLE_HZ
        duration = config.THERMAL_DURATION_S

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration or should_stop():
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

    peak_temp = max(temps) if temps else None
    peak_freq = max(freqs) if freqs else None
    min_freq_under_load = min(freqs) if freqs else None

    # Throttle heuristic: biggest observed clock drop from the peak seen.
    throttled = False
    if peak_freq and min_freq_under_load:
        if (peak_freq - min_freq_under_load) >= config.THERMAL_THROTTLE_DROP_MHZ:
            throttled = True

    overheated = bool(peak_temp and peak_temp >= config.THERMAL_CRITICAL_C)

    if peak_temp is not None:
        log(f"[THERM]   -> peak temp {peak_temp} C "
            f"(critical >= {config.THERMAL_CRITICAL_C} C)")
    if throttled:
        log(f"[THERM]   -> clock dropped {peak_freq - min_freq_under_load:.0f} MHz "
            f"under load — possible throttling")

    return {
        "temp_available": bool(temps),
        "peak_temp_c": peak_temp,
        "idle_freq_mhz": round(idle_freq) if idle_freq else None,
        "peak_freq_mhz": round(peak_freq) if peak_freq else None,
        "min_freq_mhz": round(min_freq_under_load) if min_freq_under_load else None,
        "throttled": throttled,
        "overheated": overheated,
        "samples": len(temps),
    }
