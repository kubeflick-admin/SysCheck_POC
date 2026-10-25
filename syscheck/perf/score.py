"""Performance score, label and typical-use guide (pure).

Each measurement is scored against the reference laptop in
``config.PERF_REFERENCE``: ``PERF_REFERENCE_POINTS`` at the reference
value, ``PERF_POINTS_PER_HALVING`` less for every halving, clamped to
0-100. A log scale matches how speed feels: half as fast is noticeably
slower, not useless. The sub-scores (cpu, memory, drive,
responsiveness) then combine with ``PERF_WEIGHTS``; missing ones are
left out and the weights renormalise.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Optional, Tuple

from .. import config
from .system import discrete_gpu

SUBSCORES = ("cpu", "memory", "drive", "responsiveness")
USE_OK, USE_SLOW, USE_NO = "OK", "Slow", "Not suitable"
NOT_MEASURED = "Not measured"


def scaled(measured: object, reference: float) -> Optional[float]:
    """0-100 for ``measured`` against ``reference``, or None."""
    if (not isinstance(measured, (int, float)) or isinstance(measured, bool)
            or measured <= 0 or not reference):
        return None
    points = (config.PERF_REFERENCE_POINTS + config.PERF_POINTS_PER_HALVING
              * math.log2(measured / reference))
    return max(0.0, min(100.0, points))


def weighted(parts: Mapping[str, Optional[float]],
             weights: Mapping[str, float]) -> Optional[float]:
    """Weighted mean of the parts that are not None."""
    present = {k: v for k, v in parts.items() if v is not None}
    total = sum(weights.get(k, 0.0) for k in present)
    if total <= 0:
        return None
    return sum(weights.get(k, 0.0) * v for k, v in present.items()) / total


def ram_cap(installed_gb: object) -> float:
    """The most the memory sub-score can be with this much memory."""
    if not isinstance(installed_gb, (int, float)) or installed_gb <= 0:
        return 100.0
    return float(next(cap for floor, cap in config.PERF_RAM_CAPS
                      if installed_gb >= floor))


def cpu_score(cpu: Mapping[str, object]) -> Optional[float]:
    ref = config.PERF_REFERENCE
    return weighted({"single": scaled(cpu.get("single"), ref["cpu_single"]),
                     "multi": scaled(cpu.get("multi"), ref["cpu_multi"])},
                    config.PERF_CPU_WEIGHTS)


def memory_score(memory: Mapping[str, object]) -> Optional[float]:
    speed = scaled(memory.get("bandwidth_gbps"), config.PERF_REFERENCE["memory"])
    if speed is None:
        return None
    return min(speed, ram_cap(memory.get("installed_gb")))


def drive_score(disk: Mapping[str, object]) -> Optional[float]:
    """Only an unbuffered test is scored: cached reads would flatter it."""
    if not disk.get("ran") or not disk.get("unbuffered"):
        return None
    ref = config.PERF_REFERENCE
    return weighted({
        "rand_read": scaled(disk.get("rand_read_iops"), ref["rand_read"]),
        "seq_read": scaled(disk.get("seq_read_mbps"), ref["seq_read"]),
        "seq_write": scaled(disk.get("seq_write_mbps"), ref["seq_write"]),
    }, config.PERF_DRIVE_WEIGHTS)


def responsiveness_score(stutter: Mapping[str, object]) -> Optional[float]:
    """100, less a share per stutter and freeze; gaps under full load
    count ``PERF_LOAD_GAP_FACTOR`` as much."""
    loss, measured = 0.0, False
    for phase, factor in (("idle", 1.0), ("load", config.PERF_LOAD_GAP_FACTOR)):
        gaps = stutter.get(phase)
        if not isinstance(gaps, dict) or not gaps.get("ticks"):
            continue
        measured = True
        loss += factor * (
            min(gaps["stutters"] * config.PERF_STUTTER_PENALTY,
                config.PERF_STUTTER_MAX_LOSS)
            + min(gaps["freezes"] * config.PERF_FREEZE_PENALTY,
                  config.PERF_FREEZE_MAX_LOSS))
    return max(0.0, 100.0 - loss) if measured else None


def _section(perf: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = perf.get(key)
    return value if isinstance(value, dict) else {}


def subscores(perf: Mapping[str, object]) -> Dict[str, Optional[float]]:
    return {
        "cpu": cpu_score(_section(perf, "cpu")),
        "memory": memory_score(_section(perf, "memory")),
        "drive": drive_score(_section(perf, "disk")),
        "responsiveness": responsiveness_score(_section(perf, "stutter")),
    }


def performance_score(subs: Mapping[str, Optional[float]]) -> Optional[float]:
    return weighted(subs, config.PERF_WEIGHTS)


def label_for(score: Optional[float]) -> Tuple[str, str]:
    """(label, scoring status) for a Performance score."""
    if score is None:
        return NOT_MEASURED, "inconclusive"
    return next((word, status) for floor, word, status in config.PERF_LABELS
                if score >= floor)


def use_guide(subs: Mapping[str, Optional[float]], installed_gb: object,
              gpus: object) -> List[Dict[str, object]]:
    """OK / Slow / Not suitable (or None) for each typical use, with a
    note when the missing graphics card is what holds it back."""
    rows = []
    for use, spec in config.PERF_USE_GUIDE.items():
        levels = [subs[k] for k in spec["needs"] if subs.get(k) is not None]
        if not levels:
            rows.append({"use": use, "verdict": None, "note": None})
            continue
        level = min(levels)
        rank = 2 if level >= spec["ok"] else 1 if level >= spec["slow"] else 0
        need_gb = spec.get("ram_gb")
        if need_gb and isinstance(installed_gb, (int, float)) and installed_gb:
            if installed_gb < need_gb / 2:
                rank = 0
            elif installed_gb < need_gb:
                rank = min(rank, 1)
        note = None
        if spec.get("gpu") and not discrete_gpu(gpus) and rank == 2:
            rank = 1
            note = "no separate graphics card: light or older games only"
        rows.append({"use": use, "verdict": (USE_NO, USE_SLOW, USE_OK)[rank],
                     "note": note})
    return rows
