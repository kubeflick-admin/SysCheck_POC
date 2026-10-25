"""Plain-English wording and display rows for the Performance check.

The Performance step, the Verdict card and the exported report all
describe the result through these helpers, so they always say the same
thing. Pure: no Qt, no WMI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from .. import config
from .hangs import Hang, why_it_hangs
from .score import (label_for, performance_score, ram_cap, scaled,
                    subscores, use_guide)

NA = "n/a"
_KIND_WORDS = {"hang": "programs stopped responding", "crash": "crashes",
               "bsod": "blue screens", "unexpected_shutdown":
               "unexpected shutdowns", "disk_error": "disk errors"}


@dataclass
class PerfAssessment:
    score: Optional[float]                    # 0-100, None if unmeasured
    label: str                                # Fast / Good / ... / Not measured
    status: str                               # scoring status
    subscores: Dict[str, Optional[float]]
    hangs: List[Hang]                         # most likely cause first
    uses: List[Dict[str, object]]             # typical-use guide

    @property
    def headline(self) -> str:
        if self.score is None:
            return "Performance could not be measured."
        n = len(self.hangs)
        tail = ("Nothing found that should make it hang." if not n else
                f"{n} thing{'s' if n != 1 else ''} can make it hang or feel "
                "slow.")
        return f"{self.label} ({self.score:.0f}/100). {tail}"


def _section(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _installed_gb(perf: Mapping[str, object]) -> Optional[float]:
    return (_section(perf, "memory").get("installed_gb")
            or _section(perf, "idle").get("ram_total_gb"))


def assess(perf: Mapping[str, object],
           results: Optional[Mapping[str, object]] = None) -> PerfAssessment:
    """Score, label, hang causes and use guide for one Performance run.
    ``results`` is the whole scan, for the storage / thermal links and
    the graphics card."""
    results = results or {}
    subs = subscores(perf)
    score = performance_score(subs)
    label, status = label_for(score)
    hangs = why_it_hangs(perf, results)
    if status == "pass" and any(h.serious for h in hangs):
        status = "warn"
    gpus = _section(results, "specs").get("gpus")
    return PerfAssessment(None if score is None else round(score, 1), label,
                          status, subs, hangs,
                          use_guide(subs, _installed_gb(perf), gpus))


def uses_line(uses: List[Dict[str, object]]) -> str:
    """"Browsing OK · Office OK · ... · Gaming Slow (note)"."""
    return " · ".join(f"{u['use']} {u['verdict'] or NA}"
                      + (f" ({u['note']})" if u.get("note") else "")
                      for u in uses)


# ─────────────────────────────────────────────────────────────────────
# Values
# ─────────────────────────────────────────────────────────────────────
def _ghz(mhz: object) -> Optional[str]:
    if not isinstance(mhz, (int, float)) or not mhz:
        return None
    return f"{mhz / 1000:.1f} GHz"


def _points(value: Optional[float]) -> str:
    return f"{value:.0f}/100" if value is not None else NA


def _figure(value: float) -> str:
    """1 decimal below 100, whole numbers with thousands separators above."""
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:.1f}"


def _plural(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def gap_words(gaps: object) -> str:
    """One stutter-test phase: "no stutters" / "2 stutters, 1 freeze
    (worst 310 ms)"."""
    if not isinstance(gaps, dict) or not gaps.get("ticks"):
        return NA
    if not gaps["stutters"] and not gaps["freezes"]:
        return "no stutters"
    return (f"{_plural(gaps['stutters'], 'stutter')}, "
            f"{_plural(gaps['freezes'], 'freeze')} "
            f"(worst {gaps['worst_ms']:.0f} ms)")


def _cpu_row(cpu: Mapping[str, object], sub: Optional[float]) -> str:
    clocks = []
    if _ghz(cpu.get("single_mhz")):
        clocks.append(f"{_ghz(cpu['single_mhz'])} on one core")
    if _ghz(cpu.get("multi_mhz")):
        clocks.append(f"{_ghz(cpu['multi_mhz'])} on all cores")
    text = _points(sub)
    if clocks:
        base = _ghz(cpu.get("base_mhz"))
        text += " — ran at about " + ", ".join(clocks) + (
            f" (base {base})" if base else "")
    return text


def _memory_row(memory: Mapping[str, object], sub: Optional[float]) -> str:
    parts = []
    if memory.get("installed_gb"):
        kit = [f"{memory['installed_gb']:g} GB", memory.get("type"),
               f"{memory['speed_mhz']} MHz" if memory.get("speed_mhz") else None]
        parts.append(" ".join(str(k) for k in kit if k))
    if memory.get("channels"):
        parts.append(f"{memory['channels']} channel")
    if isinstance(memory.get("modules"), int) and memory.get("slots"):
        parts.append(f"{memory['modules']} of {memory['slots']} slots used")
    if memory.get("bandwidth_gbps"):
        parts.append(f"{memory['bandwidth_gbps']:g} GB/s")
    return _points(sub) + (" — " + ", ".join(parts) if parts else "")


def _drive_row(disk: Mapping[str, object], sub: Optional[float]) -> str:
    if not disk.get("ran"):
        return f"not tested: {disk.get('reason') or 'test did not run'}"
    parts = []
    if disk.get("seq_read_mbps"):
        parts.append(f"reads {disk['seq_read_mbps']:,.0f} MB/s")
    if disk.get("seq_write_mbps"):
        parts.append(f"writes {disk['seq_write_mbps']:,.0f} MB/s")
    if disk.get("rand_read_iops"):
        each = (f" ({disk['rand_read_ms']:.2f} ms each)"
                if disk.get("rand_read_ms") else "")
        parts.append(f"{disk['rand_read_iops']:,.0f} random reads a "
                     f"second{each}")
    text = _points(sub) if disk.get("unbuffered") else "not scored"
    if parts:
        text += " — " + ", ".join(parts)
    if not disk.get("unbuffered"):
        text += " (through Windows' cache, so reads look faster than they are)"
    return text


def _history_row(history: Mapping[str, object]) -> str:
    if not history:
        return NA
    if not history.get("available"):
        return str(history.get("reason") or NA)
    counts = history.get("counts") or {}
    return " · ".join(f"{counts.get(k, 0)} {words}"
                      for k, words in _KIND_WORDS.items())


def measurement_rows(perf: Mapping[str, object],
                     subs: Optional[Dict[str, Optional[float]]] = None
                     ) -> List[Tuple[str, str]]:
    """(label, value) pairs for grids and the report."""
    subs = subs if subs is not None else subscores(perf)
    cpu, memory = _section(perf, "cpu"), _section(perf, "memory")
    disk, idle = _section(perf, "disk"), _section(perf, "idle")
    stutter, background = _section(perf, "stutter"), _section(perf, "background")
    history = _section(perf, "history")
    rows = [
        ("Processor", _cpu_row(cpu, subs.get("cpu"))),
        ("Memory", _memory_row(memory, subs.get("memory"))),
        ("Drive", _drive_row(disk, subs.get("drive"))),
        ("Responsiveness", _points(subs.get("responsiveness"))
         + f" — idle: {gap_words(stutter.get('idle'))}; under load: "
           f"{gap_words(stutter.get('load'))}"),
    ]
    if idle:
        at_idle = [f"processor {idle['cpu_pct']:.0f}%"
                   if idle.get("cpu_pct") is not None else None,
                   f"memory {idle['ram_used_pct']:.0f}% used"
                   if idle.get("ram_used_pct") is not None else None,
                   f"drive {idle['disk_busy_pct']:.0f}% busy"
                   if idle.get("disk_busy_pct") is not None else None]
        rows.append(("At idle", " · ".join(p for p in at_idle if p) or NA))
        if idle.get("commit_limit_gb"):
            rows.append(("Memory reserved",
                         f"{idle['commit_used_gb']:.1f} of "
                         f"{idle['commit_limit_gb']:.1f} GB"))
        busiest = []
        if idle.get("top_cpu"):
            busiest.append("processor: " + ", ".join(idle["top_cpu"]))
        if idle.get("top_ram"):
            busiest.append("memory: " + ", ".join(idle["top_ram"]))
        if busiest:
            rows.append(("Busiest programs", " · ".join(busiest)))
    if background:
        if background.get("free_pct") is not None:
            rows.append(("Free space", f"{background['free_pct']:.0f}% of "
                         f"{background.get('system_drive') or 'system drive'}"
                         f" ({background.get('free_gb', 0):,.0f} GB)"))
        for key, name in (("startup_count", "Startup programs"),
                          ("services_running", "Services running")):
            if background.get(key) is not None:
                rows.append((name, str(background[key])))
        power = background.get("power") or {}
        if power.get("plan") or power.get("mode"):
            text = power.get("plan") or "?"
            if power.get("mode"):
                text += f", power mode {power['mode']}"
            if power.get("battery_saver"):
                text += ", battery saver on"
            rows.append(("Power", text))
    if history:
        if history.get("install_date"):
            rows.append(("Windows installed", str(history["install_date"])))
        days = history.get("days") if history.get("available") else None
        rows.append((f"Past problems ({days or config.PERF_HISTORY_DAYS} "
                     "days)", _history_row(history)))
    return rows


def technical_lines(perf: Mapping[str, object],
                    a: PerfAssessment) -> List[str]:
    """Raw figures against the reference laptop, for "Technical details"."""
    ref = config.PERF_REFERENCE
    cpu, memory = _section(perf, "cpu"), _section(perf, "memory")
    disk, stutter = _section(perf, "disk"), _section(perf, "stutter")
    lines = []

    def against(name: str, value: object, key: str, unit: str) -> None:
        if isinstance(value, (int, float)):
            lines.append(f"{name}: {_figure(value)} {unit} (reference "
                         f"{_figure(ref[key])}) → "
                         f"{scaled(value, ref[key]):.0f}")

    against("One core", cpu.get("single"), "cpu_single", "work units/s")
    threads = cpu.get("threads")
    against(f"All {threads} threads" if threads else "All cores",
            cpu.get("multi"), "cpu_multi", "work units/s")
    if cpu.get("sustained_mhz"):
        base = (f" (base {cpu['base_mhz']} MHz)" if cpu.get("base_mhz")
                else "")
        lines.append(f"Clock held under load: {cpu['sustained_mhz']} MHz"
                     f"{base}" + (" → throttling" if cpu.get("throttled")
                                  else ""))
    against("Memory copy", memory.get("bandwidth_gbps"), "memory", "GB/s")
    cap = ram_cap(memory.get("installed_gb"))
    if cap < 100:
        lines.append(f"Memory sub-score capped at {cap:.0f} for "
                     f"{memory['installed_gb']:g} GB")
    against("Random 4 KB reads", disk.get("rand_read_iops"), "rand_read", "/s")
    against("Sequential read", disk.get("seq_read_mbps"), "seq_read", "MB/s")
    against("Sequential write", disk.get("seq_write_mbps"), "seq_write", "MB/s")
    if disk.get("ran"):
        lines.append("Drive test: " + ("unbuffered, write-through"
                                       if disk.get("unbuffered")
                                       else "buffered (not scored)")
                     + f", {disk.get('size_mb')} MB in {disk.get('folder')}")
    for phase in ("idle", "load"):
        gaps = stutter.get(phase)
        if isinstance(gaps, dict) and gaps.get("ticks"):
            lines.append(f"Stutter test ({phase}): {gaps['ticks']:,} ticks in "
                         f"{gaps['seconds']} s, {gaps['stutters']} over "
                         f"{config.PERF_STUTTER_MS} ms, {gaps['freezes']} over "
                         f"{config.PERF_FREEZE_MS} ms, worst "
                         f"{gaps['worst_ms']} ms")
    weights = " · ".join(f"{k} {w * 100:.0f}% = {_points(a.subscores.get(k))}"
                         for k, w in config.PERF_WEIGHTS.items())
    lines.append(f"Score: {weights}")
    return lines
