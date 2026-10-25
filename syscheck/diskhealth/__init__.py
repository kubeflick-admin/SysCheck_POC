"""Drive health judgement, kept free of Qt and WMI so it tests anywhere.

    health    -> Drive Health % (0-100) with reasons, and the estimated
                 remaining life
"""

from .health import (HealthResult, LifeEstimate, Reason, compute_health,
                     estimate_remaining_life)

__all__ = [
    "HealthResult",
    "LifeEstimate",
    "Reason",
    "compute_health",
    "estimate_remaining_life",
]
