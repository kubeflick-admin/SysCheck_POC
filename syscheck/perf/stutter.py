"""Stutter and freeze counting for the responsiveness test (pure).

A timer loop asks to wake every ``config.PERF_TICK_MS``; on a healthy
laptop it does, give or take a few milliseconds. A longer gap between two
wake-ups is time the laptop couldn't respond: over ``PERF_STUTTER_MS`` is
a stutter you would feel, over ``PERF_FREEZE_MS`` a freeze.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Dict, Optional, Sequence

from .. import config


def count_gaps(times: Sequence[float], stutter_ms: Optional[float] = None,
               freeze_ms: Optional[float] = None) -> Dict[str, object]:
    """Gaps between consecutive wake-up times (seconds, ascending).

    ``stutters`` counts gaps over ``stutter_ms`` up to ``freeze_ms``;
    ``freezes`` counts the longer ones, so no gap is counted twice.
    """
    stutter_ms = config.PERF_STUTTER_MS if stutter_ms is None else stutter_ms
    freeze_ms = config.PERF_FREEZE_MS if freeze_ms is None else freeze_ms
    gaps = [(b - a) * 1000 for a, b in pairwise(times)]
    return {
        "ticks": len(gaps),
        "seconds": round(times[-1] - times[0], 1) if len(times) > 1 else 0.0,
        "stutters": sum(1 for g in gaps if stutter_ms < g <= freeze_ms),
        "freezes": sum(1 for g in gaps if g > freeze_ms),
        "worst_ms": round(max(gaps), 1) if gaps else None,
    }
