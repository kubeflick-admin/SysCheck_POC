""""Why it might hang": the likely causes of freezes and slowness, each
with a plain fix, most likely first (pure).

Each rule reads the performance measurements, and some also read the
rest of the scan: the storage module's drive health and drive types,
and the thermal test's throttling. Thresholds and the ranking are in
``config`` (``PERF_*``, ``PERF_HANG_PRIORITY``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from .. import config
from ..diskhealth.describe import assess
from ..telemetry.smart_sources.schema import KIND_HDD

_FIX_DRIVERS = ("Update Windows and the laptop's drivers; if it keeps "
                "happening, the drive or memory may be faulty.")


@dataclass
class Hang:
    key: str                 # rule id, ranked by config.PERF_HANG_PRIORITY
    cause: str               # plain English: what was found
    fix: str                 # plain English: what to do about it
    serious: bool = False    # a sign of a fault, not just a slow laptop


def _section(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n:,} times"


def _gb(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


# ─────────────────────────────────────────────────────────────────────
# Rules: each returns a Hang or None
# ─────────────────────────────────────────────────────────────────────
def _drive_health(storage: Mapping[str, object]) -> Optional[Hang]:
    worst = None
    for drive in storage.get("drives") or []:
        a = assess(drive)
        pct = a.health.health_pct
        if pct is not None and pct < 85 and (
                worst is None or pct < worst.health.health_pct):
            worst = a
    if worst is None:
        return None
    return Hang("drive_health",
                f"Drive health is only {worst.health.health_pct}% "
                f"({worst.name}). A worn or failing drive is a common cause "
                "of freezes.",
                "Back up your files and replace the drive (see the Storage "
                "section).", serious=True)


def _drive_speed(disk: Mapping[str, object],
                 storage: Mapping[str, object]) -> Optional[Hang]:
    kinds = [d.get("kind") for d in storage.get("drives") or []
             if isinstance(d, dict)]
    iops = disk.get("rand_read_iops") if disk.get("unbuffered") else None
    upgrade = f"An SSD upgrade ({config.PERF_SSD_UPGRADE}) makes it much faster."
    if iops is None:
        # No speed test: judge by the drive type when it's the only kind.
        if kinds and all(k == KIND_HDD for k in kinds):
            return Hang("hdd", "Uses a slow hard disk — the main cause of "
                        "freezing.", upgrade)
        return None
    if iops < config.PERF_HDD_IOPS:
        if KIND_HDD in kinds or not kinds:
            return Hang("hdd", "Uses a slow hard disk — the main cause of "
                        "freezing.", upgrade)
        # An SSD this slow is failing or badly worn, not just old.
        return Hang("hdd", f"The drive is as slow as an old hard disk "
                    f"({iops:,.0f} reads a second) — the main cause of "
                    "freezing.", "Check its health in the Storage section. "
                    + upgrade, serious=True)
    if iops < config.PERF_SLOW_DRIVE_IOPS:
        return Hang("slow_drive", f"The drive is slow ({iops:,.0f} reads a "
                    "second), so programs open slowly.",
                    f"A faster SSD ({config.PERF_SSD_UPGRADE}) helps.")
    return None


def _memory(memory: Mapping[str, object],
            idle: Mapping[str, object]) -> List[Hang]:
    found = []
    gb = memory.get("installed_gb") or idle.get("ram_total_gb")
    used = idle.get("ram_used_pct")
    free_slot = (isinstance(memory.get("slots"), int)
                 and isinstance(memory.get("modules"), int)
                 and memory["slots"] > memory["modules"])
    more = ("Add more memory — it has a free slot." if free_slot
            else "Close programs you don't need, or add memory if the "
                 "laptop allows it.")
    if isinstance(used, (int, float)) and used > config.PERF_IDLE_RAM_WARN_PCT:
        size = f"{_gb(gb)} GB, " if gb else ""
        found.append(Hang("ram", f"{size}{used:.0f}% used at idle — will slow "
                          "down with a browser + Teams open.", more))
    elif isinstance(gb, (int, float)) and gb <= config.PERF_LOW_RAM_GB:
        found.append(Hang("low_ram", f"Only {_gb(gb)} GB of memory — too "
                          "little for Windows and a modern browser.",
                          "8 GB or more is recommended. " + more))
    commit_used, limit = idle.get("commit_used_gb"), idle.get("commit_limit_gb")
    if (not found and isinstance(commit_used, (int, float)) and limit
            and commit_used / limit * 100 > config.PERF_COMMIT_WARN_PCT):
        found.append(Hang("commit", f"Programs have reserved almost all "
                          f"memory ({_gb(commit_used)} of {_gb(limit)} GB), "
                          "so Windows will slow down or close programs.",
                          more))
    return found


def _thermal(cpu: Mapping[str, object],
             thermal: Mapping[str, object]) -> Optional[Hang]:
    ran = bool(thermal) and thermal.get("completed", True)
    if (ran and thermal.get("throttled")) or cpu.get("throttled"):
        return Hang("thermal", "Slows itself down when hot — the processor "
                    "dropped below its normal speed under load.",
                    "Needs cleaning and new thermal paste.")
    if ran and thermal.get("overheated"):
        return Hang("thermal", f"Overheats under load "
                    f"({thermal.get('peak_temp_c', 0):.0f} °C).",
                    "Needs cleaning and new thermal paste.")
    return None


def _stutter(stutter: Mapping[str, object]) -> Optional[Hang]:
    phases = [g for g in (stutter.get("idle"), stutter.get("load"))
              if isinstance(g, dict)]
    freezes = sum(g.get("freezes", 0) for g in phases)
    if freezes:
        worst = max(g.get("worst_ms") or 0 for g in phases) / 1000
        return Hang("freezes", f"Froze {_times(freezes)} during the test "
                    f"(for up to {worst:.1f} s).", _FIX_DRIVERS, serious=True)
    idle = stutter.get("idle") if isinstance(stutter.get("idle"), dict) else {}
    if idle.get("stutters", 0) >= config.PERF_IDLE_STUTTER_WARN:
        return Hang("stutter", f"Stuttered {_times(idle['stutters'])} while "
                    "doing nothing.", _FIX_DRIVERS)
    return None


def _background(idle: Mapping[str, object],
                background: Mapping[str, object]) -> List[Hang]:
    found = []
    cpu = idle.get("cpu_pct")
    if isinstance(cpu, (int, float)) and cpu > config.PERF_IDLE_CPU_WARN_PCT:
        top = idle.get("top_cpu") or []
        busiest = f" (busiest: {', '.join(top[:3])})" if top else ""
        found.append(Hang("idle_cpu", f"The processor is {cpu:.0f}% busy at "
                          f"idle{busiest}.",
                          "Remove programs you don't need, or let Windows "
                          "Update finish and check again."))
    busy = idle.get("disk_busy_pct")
    if isinstance(busy, (int, float)) and busy > config.PERF_IDLE_DISK_WARN_PCT:
        found.append(Hang("idle_disk", f"The drive is {busy:.0f}% busy at "
                          "idle.", "Usually Windows Update, search indexing "
                          "or antivirus: check again later. If it stays "
                          "busy, the drive may be too slow."))
    startup = background.get("startup_count")
    if isinstance(startup, int) and startup > config.PERF_STARTUP_WARN:
        found.append(Hang("startup", f"{startup} programs start with "
                          "Windows.", "Turn off the ones you don't need in "
                          "Task Manager > Startup apps."))
    free = background.get("free_pct")
    if isinstance(free, (int, float)) and free < config.PERF_DISK_FREE_WARN_PCT:
        drive = background.get("system_drive") or "the system drive"
        found.append(Hang("disk_full", f"Only {free:.0f}% free space on "
                          f"{drive}.", "Delete or move files (Settings > "
                          "System > Storage); keep at least 15% free."))
    power = background.get("power") if isinstance(background.get("power"),
                                                  dict) else {}
    if power.get("saver"):
        what = ("Battery saver is on" if power.get("battery_saver")
                else f"The {power.get('mode') or power.get('plan')} power "
                     "setting is on")
        found.append(Hang("power_saver", f"{what} — the laptop runs slower to "
                          "save battery.", "Switch to Balanced or Best "
                          "performance in Settings > System > Power."))
    return found


def _history(history: Mapping[str, object]) -> List[Hang]:
    if not history.get("available"):
        return []
    counts: Dict[str, int] = history.get("counts") or {}
    apps = history.get("top_apps") or {}
    days = history.get("days") or config.PERF_HISTORY_DAYS
    when = f"in the last {days} days"
    found = []
    if counts.get("disk_error"):
        found.append(Hang("disk_errors", f"Windows logged "
                          f"{counts['disk_error']} disk error"
                          f"{'s' if counts['disk_error'] != 1 else ''} {when} "
                          "— the drive may be failing.", "Back up now and "
                          "check the Storage section.", serious=True))
    if counts.get("bsod"):
        found.append(Hang("bsod", f"Windows crashed with a blue screen "
                          f"{_times(counts['bsod'])} {when}.",
                          "Often a driver or memory fault: update drivers "
                          "and run Windows Memory Diagnostic.", serious=True))
    for kind, key, verb, limit in (
            ("hang", "app_hangs", "stopped responding",
             config.PERF_HANG_EVENTS_WARN),
            ("crash", "app_crashes", "crashed", config.PERF_CRASH_EVENTS_WARN)):
        n = counts.get(kind, 0)
        if n >= limit:
            names = apps.get(kind) or []
            most = f" (most often: {', '.join(names)})" if names else ""
            found.append(Hang(key, f"Programs {verb} {_times(n)} {when}"
                              f"{most}.", "Update or reinstall those "
                              "programs; if it's many programs, the causes "
                              "above are more likely."))
    n = counts.get("unexpected_shutdown", 0)
    if n >= config.PERF_SHUTDOWN_EVENTS_WARN:
        found.append(Hang("shutdowns", f"Switched off unexpectedly "
                          f"{_times(n)} {when} (power loss, a crash or "
                          "holding the power button).", "If nobody held the "
                          "power button, check the battery and charger."))
    return found


def why_it_hangs(perf: Mapping[str, object],
                 results: Optional[Mapping[str, object]] = None) -> List[Hang]:
    """Every cause found, most likely first."""
    results = results or {}
    storage = _section(results, "storage")
    disk = _section(perf, "disk")
    idle = _section(perf, "idle")
    found = [h for h in (_drive_health(storage), _drive_speed(disk, storage),
                         _thermal(_section(perf, "cpu"),
                                  _section(results, "thermal")),
                         _stutter(_section(perf, "stutter"))) if h]
    found += _memory(_section(perf, "memory"), idle)
    found += _background(idle, _section(perf, "background"))
    found += _history(_section(perf, "history"))
    return sorted(found, key=lambda h: config.PERF_HANG_PRIORITY.get(h.key, 0),
                  reverse=True)
