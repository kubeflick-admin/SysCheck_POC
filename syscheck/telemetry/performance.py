"""Performance & hang check: how fast is this laptop, and what will make it
hang or feel slow?

Everything here only reads, except the drive test, which writes one
temporary file in %TEMP% (:mod:`.disk_speed`) and always deletes it.
Each stage is short, checks ``should_stop`` often, and degrades to
``None`` / missing fields rather than failing the scan. Windows-only
modules (``ctypes.windll``, ``winreg``) are imported inside the
functions that need them.

Stages, in order (about two minutes):

idle        CPU / memory / drive load at idle and the idle stutter test,
            sharing one ``PERF_IDLE_S`` window
cpu         a fixed zlib work unit on one core, then on every core, with
            the clock speed while it runs
load        the stutter test under full CPU load
memory      copy bandwidth, installed modules and channels
drive       the temporary-file speed test
background  startup programs, services, power plan, free space
history     the last 30 days of hangs, crashes and blue screens

Judging the numbers is :mod:`syscheck.perf` (pure).

The CPU unit is zlib compression rather than NumPy's matrix library:
NumPy's BLAS picks its code by CPU model and falls back to a slow generic
path on CPUs newer than itself, which would score the newest laptops
lowest. zlib runs the same code everywhere and releases the GIL, so
threads use every core.
"""

from __future__ import annotations

import os
import random
import subprocess
import threading
import time
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import psutil

from .. import config
from ..perf import events as ev
from ..perf.stutter import count_gaps
from ..perf.system import enabled_startup, memory_channels, power_mode
from .disk_speed import measure_drive
from .specs import _smbios_mem_type
from .thermal import is_throttled, worker_load
from .wmi_util import wmi_connection

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]
SampleFn = Callable[[str, Dict[str, object]], None]   # stage, live readings
ProgressFn = Callable[[float], None]                  # 0-100

# (key, title, planned seconds for the progress ring)
STAGES: Tuple[Tuple[str, str, float], ...] = (
    ("idle", "Idle load and stutter", config.PERF_IDLE_S),
    ("cpu", "Processor speed", 2 * config.PERF_CPU_TEST_S),
    ("load", "Stutter under load", config.PERF_LOAD_STUTTER_S),
    ("memory", "Memory speed", 3),
    ("drive", "Drive speed", 15),
    ("background", "Startup programs and settings", 3),
    ("history", "Past problems", 3),
)

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_STARTUP_FOLDERS = (("APPDATA", r"Microsoft\Windows\Start Menu\Programs\Startup"),
                    ("PROGRAMDATA",
                     r"Microsoft\Windows\Start Menu\Programs\StartUp"))
_OVERLAYS = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
_CURRENT_VERSION = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"


def _noop(*_: object) -> None:
    pass


def _run(args: List[str], timeout: float = 15) -> Optional[str]:
    """A Windows tool's output as text, or None. No console window."""
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001 - missing tool, timeout, not Windows
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout
    # wevtutil and powercfg write UTF-16 to a pipe on some systems.
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16", errors="replace").lstrip("﻿")
    return raw.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────
# Clock speed
# ─────────────────────────────────────────────────────────────────────
class ClockSampler:
    """The CPU's effective clock, as Task Manager shows it.

    Windows' ``ProcessorInformation`` counters give the nominal frequency
    and the % of it the cores run at (above 100% in turbo); psutil's own
    figure is often stuck at the base clock, so it is only the fallback.
    Use from the thread that opened ``conn`` (WMI is per thread).
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def mhz(self) -> Optional[float]:
        if self._conn is not None:
            try:
                for row in self._conn.query(
                        "SELECT ProcessorFrequency, PercentProcessorPerformance"
                        " FROM Win32_PerfFormattedData_Counters_Processor"
                        "Information WHERE Name='_Total'"):
                    nominal = float(row.ProcessorFrequency or 0)
                    pct = float(row.PercentProcessorPerformance or 0)
                    if nominal and pct:
                        return nominal * pct / 100
            except Exception:  # noqa: BLE001
                pass
        try:
            return psutil.cpu_freq().current or None
        except Exception:  # noqa: BLE001
            return None

    def base_mhz(self) -> Optional[float]:
        if self._conn is not None:
            try:
                for cpu in self._conn.Win32_Processor():
                    if cpu.MaxClockSpeed:
                        return float(cpu.MaxClockSpeed)
            except Exception:  # noqa: BLE001
                pass
        try:
            return psutil.cpu_freq().max or None
        except Exception:  # noqa: BLE001
            return None


# ─────────────────────────────────────────────────────────────────────
# Stutter timer loop
# ─────────────────────────────────────────────────────────────────────
def tick_loop(seconds: float, should_stop: StopFn,
              every_second: Callable[[float], None] = _noop
              ) -> Optional[List[float]]:
    """Wake every ``PERF_TICK_MS`` for ``seconds``; the wake-up times, or
    None if stopped. ``every_second(elapsed)`` runs between ticks."""
    interval = config.PERF_TICK_MS / 1000
    times = [time.perf_counter()]
    next_second = times[0] + 1
    while times[-1] - times[0] < seconds:
        if should_stop():
            return None
        time.sleep(interval)
        now = time.perf_counter()
        times.append(now)
        if now >= next_second:
            every_second(now - times[0])
            next_second += 1
    return times


# ─────────────────────────────────────────────────────────────────────
# Idle window: load + stutter
# ─────────────────────────────────────────────────────────────────────
def commit_charge() -> Tuple[Optional[float], Optional[float]]:
    """(committed GB, commit limit GB) from GlobalMemoryStatusEx."""
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)):
            return None, None
    except Exception:  # noqa: BLE001 - no windll off Windows
        return None, None
    limit = status.ullTotalPageFile
    used = limit - status.ullAvailPageFile
    return round(used / 2 ** 30, 1), round(limit / 2 ** 30, 1)


def _disk_busy_ms() -> Dict[str, int]:
    """Milliseconds each drive has spent servicing reads and writes."""
    try:
        return {name: c.read_time + c.write_time
                for name, c in psutil.disk_io_counters(perdisk=True).items()}
    except Exception:  # noqa: BLE001
        return {}


def _processes() -> Dict[int, psutil.Process]:
    """Every process, with its CPU counter primed."""
    procs = {}
    for proc in psutil.process_iter(["name"]):
        try:
            proc.cpu_percent(None)
            procs[proc.pid] = proc
        except (psutil.Error, OSError):
            continue
    return procs


def _top_processes(procs: Dict[int, psutil.Process]
                   ) -> Tuple[List[str], List[str]]:
    """The 5 busiest program names by CPU and by memory (same-named
    processes added together; SysCheck itself and the idle task left out)."""
    cpus = psutil.cpu_count(logical=True) or 1
    cpu: Dict[str, float] = defaultdict(float)
    ram: Dict[str, int] = defaultdict(int)
    me = os.getpid()
    for pid, proc in procs.items():
        name = proc.info.get("name")
        if pid in (0, me) or not name or name in ("System Idle Process",
                                                  "Idle"):
            continue
        try:
            cpu[name] += proc.cpu_percent(None) / cpus
            ram[name] += proc.memory_info().rss
        except (psutil.Error, OSError):
            continue
    top_cpu = [n for n, v in sorted(cpu.items(), key=lambda x: -x[1])
               if v >= 0.5][:5]
    top_ram = [n for n, _ in sorted(ram.items(), key=lambda x: -x[1])][:5]
    return top_cpu, top_ram


def measure_idle(should_stop: StopFn, on_sample: SampleFn = _noop,
                 seconds: Optional[float] = None
                 ) -> Optional[Tuple[Dict[str, object], Dict[str, object]]]:
    """(idle load, idle stutter gaps) from one shared window, or None.
    Process lists are read before and after the window, never during it,
    so their cost can't show up as stutter."""
    seconds = seconds or config.PERF_IDLE_S
    procs = _processes()
    psutil.cpu_percent(None)
    busy0, t0 = _disk_busy_ms(), time.perf_counter()
    cpu: List[float] = []
    ram: List[float] = []

    def every_second(elapsed: float) -> None:
        cpu.append(psutil.cpu_percent(None))
        ram.append(psutil.virtual_memory().percent)
        on_sample("idle", {"elapsed": elapsed, "cpu_pct": cpu[-1],
                           "ram_pct": ram[-1]})

    times = tick_loop(seconds, should_stop, every_second)
    if times is None:
        return None
    window_ms = (time.perf_counter() - t0) * 1000
    busy1 = _disk_busy_ms()
    disk = max((min(100.0, (busy1[d] - busy0[d]) / window_ms * 100)
                for d in busy1 if d in busy0), default=None)
    vm = psutil.virtual_memory()
    commit_used, commit_limit = commit_charge()
    top_cpu, top_ram = _top_processes(procs)
    idle = {
        "cpu_pct": round(sum(cpu) / len(cpu), 1) if cpu else None,
        "ram_used_pct": round(sum(ram) / len(ram), 1) if ram else vm.percent,
        "ram_used_gb": round(vm.used / 2 ** 30, 1),
        "ram_total_gb": round(vm.total / 2 ** 30, 1),
        "commit_used_gb": commit_used,
        "commit_limit_gb": commit_limit,
        "disk_busy_pct": round(disk, 1) if disk is not None else None,
        "top_cpu": top_cpu,
        "top_ram": top_ram,
    }
    return idle, count_gaps(times)


def measure_load_stutter(should_stop: StopFn, on_sample: SampleFn = _noop,
                         seconds: Optional[float] = None
                         ) -> Optional[Dict[str, object]]:
    """Stutter gaps while every core runs the thermal test's load."""
    seconds = seconds or config.PERF_LOAD_STUTTER_S
    stop = threading.Event()
    load = [threading.Thread(target=worker_load, args=(stop,), daemon=True)
            for _ in range(psutil.cpu_count(logical=True) or 1)]
    for t in load:
        t.start()
    try:
        times = tick_loop(seconds, should_stop,
                          lambda el: on_sample("load", {"elapsed": el}))
    finally:
        stop.set()
        for t in load:
            t.join(timeout=2.0)
    return count_gaps(times) if times else None


# ─────────────────────────────────────────────────────────────────────
# CPU
# ─────────────────────────────────────────────────────────────────────
_ZLIB_DATA: Optional[bytes] = None


def _zlib_data() -> bytes:
    """256 KB of word-like text: compressible, but real work (fixed seed,
    so every laptop compresses exactly the same bytes)."""
    global _ZLIB_DATA
    if _ZLIB_DATA is None:
        rng = random.Random(1234)
        words = [bytes(rng.choices(b"abcdefghijklmnopqrstuvwxyz",
                                   k=rng.randint(2, 9)))
                 for _ in range(4000)]
        _ZLIB_DATA = b" ".join(rng.choice(words)
                               for _ in range(60_000))[:256 * 1024]
    return _ZLIB_DATA


def work_unit() -> None:
    """One fixed piece of CPU work (about 5 ms on the reference laptop)."""
    zlib.compress(_zlib_data(), 6)


def run_units(threads: int, seconds: float, should_stop: StopFn,
              every_second: Callable[[], None] = _noop) -> Optional[float]:
    """Work units per second of work time, summed over ``threads``
    threads that each run for ``seconds``; None if stopped."""
    _zlib_data()
    counts = [0] * threads
    work = [0.0] * threads
    go, halt = threading.Event(), threading.Event()

    def worker(i: int) -> None:
        work_unit()                         # warm-up
        go.wait()
        end = time.perf_counter() + seconds
        while time.perf_counter() < end and not halt.is_set():
            t = time.perf_counter()
            work_unit()
            work[i] += time.perf_counter() - t
            counts[i] += 1

    pool = [threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(threads)]
    for t in pool:
        t.start()
    go.set()
    last = time.perf_counter()
    while any(t.is_alive() for t in pool):
        pool[0].join(timeout=0.25)
        if should_stop():
            halt.set()
        if time.perf_counter() - last >= 1.0:
            every_second()
            last = time.perf_counter()
    if halt.is_set():
        return None
    return sum(c / w for c, w in zip(counts, work, strict=True) if w) or None


def measure_cpu(clock: ClockSampler, should_stop: StopFn,
                on_sample: SampleFn = _noop,
                seconds: Optional[float] = None) -> Optional[Dict[str, object]]:
    """One-core and all-core work units per second, with the clock speed
    sampled each second; None if stopped."""
    seconds = seconds or config.PERF_CPU_TEST_S
    threads = psutil.cpu_count(logical=True) or 1
    one: List[float] = []
    every: List[float] = []

    def sampler(into: List[float], phase: str):
        def sample() -> None:
            mhz = clock.mhz()
            if mhz:
                into.append(mhz)
            on_sample("cpu", {"phase": phase, "mhz": mhz})
        return sample

    single = run_units(1, seconds, should_stop, sampler(one, "single"))
    if single is None:
        return None
    multi = run_units(threads, seconds, should_stop, sampler(every, "multi"))
    if multi is None:
        return None
    base = clock.base_mhz()
    sustained, throttled = is_throttled(every, base, max(1, len(every) // 2))
    return {
        "single": round(single, 1),
        "multi": round(multi, 1),
        "threads": threads,
        "single_mhz": round(sum(one) / len(one)) if one else None,
        "multi_mhz": round(sum(every) / len(every)) if every else None,
        "sustained_mhz": round(sustained) if sustained else None,
        "base_mhz": round(base) if base else None,
        "throttled": throttled,
    }


# ─────────────────────────────────────────────────────────────────────
# Memory
# ─────────────────────────────────────────────────────────────────────
def memory_bandwidth(size_mb: Optional[int] = None,
                     repeats: int = 8) -> Optional[float]:
    """Copy bandwidth in GB/s (bytes read + written), best of ``repeats``.
    Uses a smaller array when the laptop is short of free memory."""
    size_mb = size_mb or config.PERF_MEMORY_TEST_MB
    try:
        free_mb = psutil.virtual_memory().available // 2 ** 20
        size_mb = max(16, min(size_mb, free_mb // 4))
        src = np.ones(size_mb * 2 ** 20 // 8)
        dst = np.empty_like(src)
    except (MemoryError, ValueError):
        return None
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        np.copyto(dst, src)
        best = min(best, time.perf_counter() - t)
    return round(2 * src.nbytes / best / 1e9, 1) if best > 0 else None


def memory_modules(conn) -> Tuple[List[Dict[str, object]], Optional[int]]:
    """Installed modules from ``Win32_PhysicalMemory`` and the slot count
    from ``Win32_PhysicalMemoryArray``."""
    if conn is None:
        return [], None
    modules = []
    try:
        for stick in conn.Win32_PhysicalMemory():
            speed = (getattr(stick, "ConfiguredClockSpeed", None)
                     or stick.Speed)
            kind = getattr(stick, "SMBIOSMemoryType", None)
            modules.append({
                "capacity_gb": int(stick.Capacity or 0) / 2 ** 30,
                "speed_mhz": int(speed) if speed else None,
                "type": _smbios_mem_type(int(kind)) if kind else None,
                "locator": stick.DeviceLocator, "bank": stick.BankLabel,
            })
    except Exception:  # noqa: BLE001
        return [], None
    slots = None
    try:
        slots = sum(int(a.MemoryDevices or 0)
                    for a in conn.Win32_PhysicalMemoryArray()) or None
    except Exception:  # noqa: BLE001
        pass
    return modules, slots


def measure_memory(conn) -> Dict[str, object]:
    modules, slots = memory_modules(conn)
    installed = sum(m["capacity_gb"] for m in modules)
    speeds = [m["speed_mhz"] for m in modules if m["speed_mhz"]]
    kinds = [m["type"] for m in modules if m["type"]]
    return {
        "bandwidth_gbps": memory_bandwidth(),
        "installed_gb": round(installed, 1) if installed else
        float(round(psutil.virtual_memory().total / 2 ** 30)),
        "modules": len(modules) or None,
        "slots": slots,
        "speed_mhz": min(speeds) if speeds else None,
        "type": kinds[0] if kinds else None,
        "channels": memory_channels(modules),
    }


# ─────────────────────────────────────────────────────────────────────
# Background: startup, services, power, free space
# ─────────────────────────────────────────────────────────────────────
def _reg_values(hive, path: str) -> Dict[str, object]:
    import winreg
    try:
        with winreg.OpenKey(hive, path) as key:
            values = {}
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                values[name] = value
            return values
    except OSError:
        return {}


def startup_programs() -> Optional[List[str]]:
    """Programs that start with Windows for this user: the HKCU / HKLM
    Run keys and both Startup folders, less the ones turned off in Task
    Manager. (``Win32_StartupCommand`` lists every user's Run keys and
    can't see Task Manager's switches, so it isn't used.)"""
    try:
        import winreg
    except ImportError:
        return None
    entries: List[Tuple[str, str]] = []
    for hive, path, group in ((winreg.HKEY_CURRENT_USER, _RUN, "Run"),
                              (winreg.HKEY_LOCAL_MACHINE, _RUN, "Run"),
                              (winreg.HKEY_LOCAL_MACHINE, _RUN32, "Run32")):
        entries += [(group, name) for name in _reg_values(hive, path) if name]
    for env, sub in _STARTUP_FOLDERS:
        folder = Path(os.environ.get(env, "")) / sub
        try:
            entries += [("StartupFolder", f.name) for f in folder.iterdir()
                        if f.is_file() and f.name.lower() != "desktop.ini"]
        except OSError:
            continue
    approved: Dict[Tuple[str, str], bytes] = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for group in ("Run", "Run32", "StartupFolder"):
            for name, value in _reg_values(hive, rf"{_APPROVED}\{group}").items():
                if isinstance(value, bytes):
                    approved[(group, name.lower())] = value
    return [n[:-4] if n.lower().endswith(".lnk") else n
            for n in enabled_startup(entries, approved)]


def _running_services() -> Optional[int]:
    if not hasattr(psutil, "win_service_iter"):
        return None
    try:
        running = 0
        for service in psutil.win_service_iter():
            try:
                running += service.status() == "running"
            except (psutil.Error, OSError):
                continue
        return running
    except Exception:  # noqa: BLE001
        return None


def _battery_saver() -> bool:
    """Windows 10+ battery / energy saver (GetSystemPowerStatus)."""
    try:
        import ctypes

        class PowerStatus(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte),
                        ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong),
                        ("BatteryFullLifeTime", ctypes.c_ulong)]

        status = PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(
                ctypes.byref(status)):
            return False
        return status.SystemStatusFlag == 1
    except Exception:  # noqa: BLE001
        return False


def power_settings() -> Optional[Dict[str, object]]:
    scheme = _run(["powercfg", "/getactivescheme"])
    if scheme is None:
        return None
    overlay = None
    try:
        import winreg
        battery = psutil.sensors_battery()
        on_ac = battery is None or battery.power_plugged is not False
        values = _reg_values(winreg.HKEY_LOCAL_MACHINE, _OVERLAYS)
        overlay = values.get("ActiveOverlayAcPowerScheme" if on_ac
                             else "ActiveOverlayDcPowerScheme")
    except Exception:  # noqa: BLE001
        pass
    return power_mode(scheme, overlay if isinstance(overlay, str) else None,
                      _battery_saver())


def collect_background() -> Dict[str, object]:
    startup = startup_programs()
    system = os.environ.get("SystemDrive") if os.name == "nt" else None
    root = system + "\\" if system else os.path.abspath(os.sep)
    out: Dict[str, object] = {
        "startup_count": len(startup) if startup is not None else None,
        "startup_names": startup,
        "services_running": _running_services(),
        "power": power_settings(),
        "system_drive": system or root,
    }
    try:
        usage = psutil.disk_usage(root)
        out["free_pct"] = round(100 - usage.percent, 1)
        out["free_gb"] = round(usage.free / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    return out


# ─────────────────────────────────────────────────────────────────────
# History: event logs and install date
# ─────────────────────────────────────────────────────────────────────
def install_date() -> Optional[datetime]:
    try:
        import winreg
        value = _reg_values(winreg.HKEY_LOCAL_MACHINE,
                            _CURRENT_VERSION).get("InstallDate")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(value, int) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def collect_history(log: LogFn = _noop) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    events: List[Dict[str, object]] = []
    read_ok = True
    for log_name, ids in ev.LOG_EVENT_IDS.items():
        text = _run(["wevtutil", "qe", log_name,
                     f"/q:{ev.xpath(ids, config.PERF_HISTORY_DAYS)}",
                     "/f:xml", "/rd:true", f"/c:{config.PERF_EVENT_MAX}"])
        if text is None:
            log(f"[PERF] Could not read the {log_name} event log.")
            read_ok = False
            continue
        events += ev.parse_events(text)
    first = ev.parse_events(_run(["wevtutil", "qe", "System", "/c:1",
                                  "/f:xml"]) or "")
    oldest = first[0]["time"] if first else None
    installed = install_date()
    status = ev.history_status(installed, oldest, now, read_ok)
    return {**status, **ev.summarise(events, now),
            "install_date": installed.strftime("%d %b %Y") if installed
            else None}


# ─────────────────────────────────────────────────────────────────────
# The whole check
# ─────────────────────────────────────────────────────────────────────
def run_performance_check(log: Optional[LogFn] = None,
                          on_stage: Optional[Callable[[str, str], None]] = None,
                          on_sample: Optional[SampleFn] = None,
                          on_progress: Optional[ProgressFn] = None,
                          should_stop: Optional[StopFn] = None,
                          stages: Optional[Tuple[str, ...]] = None
                          ) -> Dict[str, object]:
    """Run every stage (or only ``stages``) and return the measurements.

    ``completed`` is False when stopped early: the result is then left
    out of the grade, like an early-stopped thermal test.
    """
    log = log or _noop
    on_stage = on_stage or _noop
    on_sample = on_sample or _noop
    on_progress = on_progress or _noop
    should_stop = should_stop or (lambda: False)
    plan = [s for s in STAGES if stages is None or s[0] in stages]
    total = sum(s[2] for s in plan) or 1
    result: Dict[str, object] = {"completed": False, "stutter": {}}

    with wmi_connection() as conn:
        clock = ClockSampler(conn)
        done = 0.0
        for key, title, planned in plan:
            if should_stop():
                break
            on_stage(key, title)
            on_progress(done / total * 100)
            log(f"[PERF] {title} ...")

            def sample(stage: str, data: Dict[str, object],
                       _done=done, _planned=planned) -> None:
                elapsed = data.get("elapsed")
                if isinstance(elapsed, (int, float)):
                    on_progress((_done + min(elapsed, _planned)) / total * 100)
                on_sample(stage, data)

            try:
                if key == "idle":
                    out = measure_idle(should_stop, sample)
                    if out:
                        result["idle"], result["stutter"]["idle"] = out
                elif key == "cpu":
                    result["cpu"] = measure_cpu(clock, should_stop, sample)
                elif key == "load":
                    result["stutter"]["load"] = measure_load_stutter(
                        should_stop, sample)
                elif key == "memory":
                    result["memory"] = measure_memory(conn)
                elif key == "drive":
                    result["disk"] = measure_drive(
                        should_stop=should_stop, log=log,
                        on_sample=lambda d: sample("drive", d))
                elif key == "background":
                    result["background"] = collect_background()
                elif key == "history":
                    result["history"] = collect_history(log)
            except Exception as exc:  # noqa: BLE001
                log(f"[PERF] {title} failed: {exc}")
            done += planned
        else:
            result["completed"] = not should_stop()
    on_progress(100)
    log("[PERF] Performance check " + ("complete." if result["completed"]
                                      else "stopped early."))
    return result
