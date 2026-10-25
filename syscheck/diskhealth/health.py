"""Drive Health % and estimated remaining life (pure: no Qt, no WMI).

Health % follows the approach Hard Disk Sentinel describes in its public
help pages, with our own weights from ``config``:

* data method: health starts at 1.0 and every health-relevant count
  takes ``min(weight * count, max_loss)`` away, multiplicatively;
* vendor method: for ATA drives, each attribute's normalised value
  against its threshold, multiplied together;
* the lower of the two wins, after caps for a failing drive, a value at
  its threshold, SSD wear and a low NVMe spare.

Power-on hours never lower health; they shorten the remaining life.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .. import config
from ..telemetry.smart_sources.schema import (FLAG_FAILING, FLAG_WARNING,
                                              KIND_HDD, KIND_UNKNOWN,
                                              SOURCE_NONE, SOURCE_RELIABILITY,
                                              as_int)

SEVERITY_RANK = {"info": 0, "warn": 1, "fail": 2}

METHOD_DATA = "data"          # raw counts and caps
METHOD_VENDOR = "vendor"      # the manufacturer's normalised values were lower
METHOD_STATUS = "status"      # only a pass / fail flag could be read
METHOD_NONE = "none"          # nothing readable

METHOD_LABELS = {
    METHOD_DATA: "attribute counts",
    METHOD_VENDOR: "manufacturer values",
    METHOD_STATUS: "drive status only",
    METHOD_NONE: "not readable",
}

# A problem that leaves less than this share of health is a failure, in
# line with the score's red band (scoring._band_from_status).
_FAIL_BELOW = 0.60

# NVMe critical-warning bits (SMART / Health Information log, byte 0).
NVME_WARNING_BITS = {
    0x01: "spare space below its limit",
    0x02: "temperature outside its limits",
    0x04: "reliability degraded by internal errors",
    0x08: "switched to read-only",
    0x10: "memory backup failed",
}


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


# What each penalised counter means to a buyer.
_PENALTY_TEXT = {
    5: lambda n: (f"{_plural(n, 'damaged area')} found on the drive. They are "
                  "contained for now, but it is a sign of wear. Keep backups."),
    196: lambda n: ("The drive has had to move data away from damaged areas "
                    f"{_plural(n, 'time')}."),
    197: lambda n: (f"{_plural(n, 'unstable area')} the drive can't read "
                    "reliably. Data stored there may be lost; back it up now."),
    198: lambda n: (f"{_plural(n, 'area')} could not be read even after "
                    "retrying. Some data may already be lost."),
    187: lambda n: (f"The drive reported {_plural(n, 'read error')} it could "
                    "not fix."),
    188: lambda n: (f"{_plural(n, 'operation')} timed out. This can point to a "
                    "failing drive or a power or connection problem."),
    10: lambda n: (f"The disk needed {_plural(n, 'extra attempt')} to spin up. "
                   "Its motor may be wearing out."),
    189: lambda n: ("The read/write head flew too high during "
                    f"{_plural(n, 'write')}, often after knocks or drops."),
    184: lambda n: (f"{_plural(n, 'internal data error')} detected inside the "
                    "drive."),
    "nvme_media": lambda n: (f"The SSD recorded {_plural(n, 'data integrity error')}"
                             ". Keep backups."),
    "windows": lambda n: (f"Windows recorded {_plural(n, 'read or write error')}"
                          " the drive could not fix."),
}

_PENALTY_NAMES = {
    5: "Reallocated sectors", 196: "Reallocation events",
    197: "Current pending sectors", 198: "Offline uncorrectable",
    187: "Reported uncorrectable", 188: "Command timeouts",
    10: "Spin retries", 189: "High fly writes", 184: "End-to-end errors",
    "nvme_media": "NVMe media and data integrity errors",
    "windows": "Windows uncorrected read + write errors",
}


@dataclass
class Reason:
    """One finding behind a Health %."""

    text: str                 # plain English, for the buyer
    detail: str               # technical: attribute, raw value, effect
    severity: str = "warn"    # info | warn | fail
    loss: float = 0.0         # share of health it removed (0-1), for ranking
    key: object = ""          # what it is about: attribute id, "wear", ...


@dataclass
class HealthResult:
    health_pct: Optional[int]     # 0-100, or None when nothing was readable
    reasons: List[Reason] = field(default_factory=list)   # worst first
    method: str = METHOD_NONE
    vendor_pct: Optional[int] = None   # manufacturer-based health (ATA only)

    @property
    def worst(self) -> Optional[Reason]:
        return self.reasons[0] if self.reasons else None

    def technical(self) -> List[str]:
        """Lines for a "Technical details" section."""
        lines = [r.detail for r in self.reasons]
        if self.vendor_pct is not None:
            lines.append(f"Manufacturer-based health: {self.vendor_pct}%")
        lines.append(f"Health method: {METHOD_LABELS[self.method]}")
        return lines


@dataclass
class LifeEstimate:
    hours: Optional[float]     # powered-on hours left, capped
    days: Optional[float]      # calendar days at the assumed daily use
    text: str                  # "more than 2 years", "unknown", ...
    basis: str = "none"        # "age" | "wear" | "none"
    rough: bool = False        # power-on time itself was estimated

    @property
    def replace_soon(self) -> bool:
        return self.days is not None and self.days < 90


# ─────────────────────────────────────────────────────────────────────
# Attribute helpers
# ─────────────────────────────────────────────────────────────────────
def attributes(drive: Dict[str, object]) -> List[Dict[str, object]]:
    """The drive's ATA attribute table (smartctl or WMI blob), or []."""
    table = (drive.get("raw") or {}).get("attributes") or []
    return [a for a in table if isinstance(a, dict)]


def find_attr(attrs: List[Dict[str, object]],
              attr_id: int) -> Optional[Dict[str, object]]:
    return next((a for a in attrs if a.get("id") == attr_id), None)


def attribute_count(attr: Dict[str, object]) -> Optional[int]:
    """The raw count of an ATA attribute, or None.

    Some vendors pack three 16-bit counters into 188 (command timeouts);
    only the low one is counted.
    """
    raw = attr.get("raw")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    return raw & 0xFFFF if attr.get("id") == 188 else raw


def penalised(attr_id: object, kind: object) -> bool:
    """True when attribute ``attr_id`` counts towards health on ``kind``."""
    spec = config.HEALTH_PENALTIES.get(attr_id)
    if spec is None:
        return False
    return spec[2] == "all" or kind in (KIND_HDD, KIND_UNKNOWN)


def threshold_crossed(attr: Dict[str, object]) -> bool:
    """Normalised value at or below a real (non-zero) threshold."""
    value, thresh = attr.get("value"), attr.get("thresh")
    return (isinstance(value, int) and isinstance(thresh, int)
            and thresh > 0 and value <= thresh)


def _attr_label(attr: Dict[str, object]) -> str:
    name = str(attr.get("name") or "Unknown attribute").replace("_", " ")
    return f"{name} (ID {attr.get('id')})"


def _pct(fraction: float) -> int:
    return int(round(max(0.0, min(1.0, fraction)) * 100))


# ─────────────────────────────────────────────────────────────────────
# Health %
# ─────────────────────────────────────────────────────────────────────
def _counts(drive: Dict[str, object], attrs: List[Dict[str, object]]
            ) -> Iterator[Tuple[object, int, Tuple[float, float]]]:
    """(key, count, (weight, max loss)) for each health counter read."""
    kind = drive.get("kind")
    if attrs:
        for attr_id, (weight, max_loss, _) in config.HEALTH_PENALTIES.items():
            attr = find_attr(attrs, attr_id) if penalised(attr_id, kind) else None
            count = attribute_count(attr) if attr else None
            if count is not None:
                yield attr_id, count, (weight, max_loss)
    else:
        # Sources without a table (Windows reliability counters).
        for attr_id, key in ((5, "reallocated_sectors"),
                             (197, "pending_sectors")):
            count = as_int(drive.get(key))
            if count is not None:
                yield attr_id, count, config.HEALTH_PENALTIES[attr_id][:2]
        count = as_int(drive.get("uncorrectable_errors"))
        if count is not None:
            yield "windows", count, config.HEALTH_WINDOWS_UNCORRECTED_PENALTY
    count = as_int(drive.get("media_errors"))
    if count is not None:
        yield "nvme_media", count, config.HEALTH_NVME_MEDIA_PENALTY


def vendor_health(drive: Dict[str, object]
                  ) -> Tuple[Optional[float], Optional[Dict[str, object]]]:
    """Manufacturer-based health (0-1) and the attribute that scored lowest.

    Each penalised attribute scores ``(value - thresh) / (new - thresh)``,
    where ``new`` is the value a fresh drive starts at: 100, or 200 / 253
    for attributes whose values run above 100. None without a table.
    """
    kind = drive.get("kind")
    product, lowest, lowest_cond = None, None, 2.0
    for attr in attributes(drive):
        value, thresh = attr.get("value"), attr.get("thresh")
        if (not penalised(attr.get("id"), kind) or not isinstance(value, int)
                or not isinstance(thresh, int)):
            continue
        new = 100 if value <= 100 else 200 if value <= 200 else 253
        if thresh >= new:
            cond = 1.0 if value > thresh else 0.0
        else:
            cond = min(1.0, max(0.0, (value - thresh) / (new - thresh)))
        product = cond if product is None else product * cond
        if cond < lowest_cond:
            lowest, lowest_cond = attr, cond
    return product, lowest


def _failing_reason(drive: Dict[str, object]) -> Reason:
    cap = config.HEALTH_CAP_FAILING_PCT
    raw = drive.get("raw") or {}
    critical = as_int(raw.get("nvme_critical_warning"))
    if critical:
        bits = [text for bit, text in NVME_WARNING_BITS.items()
                if critical & bit]
        return Reason(
            "The SSD has raised a critical warning ("
            + "; ".join(bits or ["unknown cause"])
            + "). Back up your data now.",
            f"NVMe critical warning 0x{critical:02X} → health capped at "
            f"{cap}%", "fail", 1.0, "failing")
    if drive.get("source") in (SOURCE_RELIABILITY, SOURCE_NONE):
        return Reason(
            "Windows reports this drive as unhealthy. Back up your data now "
            "and replace the drive.",
            f"Windows HealthStatus: Unhealthy → health capped at {cap}%",
            "fail", 1.0, "failing")
    return Reason(
        "The drive's own self-check says it is failing. Back up your data "
        "now and replace the drive.",
        f"S.M.A.R.T. overall health: FAILED → health capped at {cap}%",
        "fail", 1.0, "failing")


def _wear_reason(used: int) -> Reason:
    detail = (f"Percentage used {used}% → health at most "
              f"{max(0, 100 - used)}%")
    if used < 50:
        return Reason(f"{used}% of rated life used, plenty left.", detail,
                      "info", used / 100, "wear")
    if used < config.SSD_WEAR_WARN_PCT:
        return Reason(f"{used}% of rated life used, a fair amount left.",
                      detail, "info", used / 100, "wear")
    if used < 100:
        return Reason(f"{used}% of rated life used. It is nearing the end of "
                      "its rated life; plan to replace it.", detail, "warn",
                      used / 100, "wear")
    return Reason(f"All of its rated life is used up ({used}%). It may keep "
                  "working, but could fail without warning; replace it.",
                  detail, "fail", 1.0, "wear")


def compute_health(drive: Dict[str, object]) -> HealthResult:
    """Drive Health % (0-100) from a normalised drive record."""
    attrs = attributes(drive)
    reasons: List[Reason] = []
    health = 1.0
    evidence = bool(attrs)

    # Data method: every count takes its share, multiplicatively.
    for key, count, (weight, max_loss) in _counts(drive, attrs):
        evidence = True
        if count <= 0:
            continue
        loss = min(weight * count, max_loss)
        health *= 1 - loss
        label = _PENALTY_NAMES[key] + (f" (ID {key})" if isinstance(key, int)
                                       else "")
        reasons.append(Reason(
            _PENALTY_TEXT[key](count),
            f"{label}: {count:,} → -{loss * 100:.1f}% "
            f"(weight {weight * 100:g}% each, max {max_loss * 100:g}%)",
            "warn" if 1 - loss >= _FAIL_BELOW else "fail", loss, key))

    crc = as_int(drive.get("crc_errors"))
    if crc:
        reasons.append(Reason(
            f"{_plural(crc, 'data transfer error')} between the drive and the "
            "laptop. This usually means a loose or faulty connector or cable, "
            "not a worn drive.",
            f"UDMA CRC errors (ID 199): {crc:,} → no effect on health "
            "(cable / connector)", "info", 0.0, 199))

    # Caps: health can't be better than what the drive itself says.
    flag = drive.get("health_flag")
    if flag == FLAG_FAILING:
        health = min(health, config.HEALTH_CAP_FAILING_PCT / 100)
        reasons.append(_failing_reason(drive))
    elif flag == FLAG_WARNING:
        cap = config.HEALTH_CAP_WARNING_PCT
        health = min(health, cap / 100)
        reasons.append(Reason(
            "Windows reports a warning for this drive. Keep backups and check "
            "it again soon.",
            f"Windows HealthStatus: Warning → health capped at {cap}%",
            "warn", 1 - cap / 100, "warning"))

    crossed = [a for a in attrs if threshold_crossed(a)
               and a.get("id") not in config.HEALTH_THRESHOLD_IGNORE_IDS]
    if crossed:
        cap = config.HEALTH_CAP_THRESHOLD_PCT
        health = min(health, cap / 100)
        names = ", ".join(str(a.get("name") or f"ID {a.get('id')}")
                          .replace("_", " ") for a in crossed)
        reasons.append(Reason(
            f"The drive's own rating for {names} has reached the "
            "manufacturer's failure limit. Back up your data now.",
            "At or below threshold: " + ", ".join(
                f"{_attr_label(a)} value {a['value']} <= {a['thresh']}"
                for a in crossed) + f" → health capped at {cap}%",
            "fail", 1.0, "threshold"))

    used = as_int(drive.get("percentage_used"))
    if used is not None:
        evidence = True
        if used > 0:
            health = min(health, max(0, 100 - used) / 100)
            reasons.append(_wear_reason(used))

    spare = as_int(drive.get("available_spare_pct"))
    floor = as_int(drive.get("available_spare_threshold_pct"))
    if spare is not None:
        evidence = True
        if floor is not None and spare < floor:
            cap = config.HEALTH_CAP_SPARE_PCT
            health = min(health, cap / 100)
            reasons.append(Reason(
                "The SSD has almost run out of spare space for replacing "
                f"worn-out cells ({spare}% left, limit {floor}%). Replace it "
                "soon.",
                f"NVMe available spare {spare}% < threshold {floor}% → "
                f"health capped at {cap}%", "fail", 1.0, "spare"))

    if not evidence:
        # Only a status flag (or nothing at all) could be read.
        if flag in (FLAG_FAILING, FLAG_WARNING):
            return HealthResult(_pct(health), _ranked(reasons), METHOD_STATUS)
        return HealthResult(None, _ranked(reasons), METHOD_NONE)

    pct = data_pct = _pct(health)
    method = METHOD_DATA
    vendor, lowest = vendor_health(drive)
    vendor_pct = _pct(vendor) if vendor is not None else None
    if vendor_pct is not None and vendor_pct < data_pct:
        pct, method = vendor_pct, METHOD_VENDOR
        reasons.append(Reason(
            f"The drive's own health ratings put it at {vendor_pct}%.",
            f"Manufacturer-based health {vendor_pct}% is below the "
            f"count-based {data_pct}% (lowest: {_attr_label(lowest)} value "
            f"{lowest['value']}, threshold {lowest['thresh']})",
            "info" if vendor_pct >= 85
            else "warn" if vendor_pct >= _FAIL_BELOW * 100 else "fail",
            1 - vendor, "vendor"))
    return HealthResult(pct, _ranked(reasons), method, vendor_pct)


def _ranked(reasons: List[Reason]) -> List[Reason]:
    return sorted(reasons, key=lambda r: (SEVERITY_RANK[r.severity], r.loss),
                  reverse=True)


# ─────────────────────────────────────────────────────────────────────
# Remaining life
# ─────────────────────────────────────────────────────────────────────
def life_words(days: Optional[float]) -> str:
    """Calendar days left → wording that never sounds exact."""
    if days is None:
        return "unknown"
    if days >= config.LIFE_CAP_DAYS:
        return f"more than {config.LIFE_CAP_DAYS} days"
    if days >= 730:
        return "more than 2 years"
    if days >= 90:
        return f"about {round(days / 30.44)} months of typical use"
    return "less than 3 months — replace soon"


def estimate_remaining_life(drive: Dict[str, object],
                            health_pct: Optional[int]) -> LifeEstimate:
    """Estimated remaining life from age, heat, health and SSD wear.

    ``expected - power-on hours``, less a heat penalty, scaled by
    ``(health / 100) ** 2``; for a worn SSD, the wear rate so far may
    give a lower figure. Converted to calendar time at
    ``config.LIFE_HOURS_PER_DAY`` and capped at ``config.LIFE_CAP_DAYS``.
    """
    poh = as_int(drive.get("power_on_hours"))
    if health_pct is None or poh is None:
        return LifeEstimate(None, None, life_words(None))

    family = "HDD" if drive.get("kind") in (KIND_HDD, KIND_UNKNOWN) else "SSD"
    expected = config.EXPECTED_LIFE_HOURS[family]
    remaining = float(expected - poh)
    temps = [t for t in (as_int(drive.get("temperature_c")),
                         as_int(drive.get("temperature_max_c")))
             if t is not None]
    if temps and max(temps) >= config.LIFE_HOT_C[family]:
        remaining -= expected * config.LIFE_HEAT_PENALTY_FRAC
    remaining *= (health_pct / 100) ** 2
    basis = "age"

    used = as_int(drive.get("percentage_used"))
    if used and used > 0 and poh > 0:
        by_wear = poh / used * max(0, 100 - used)
        if by_wear < remaining:
            remaining, basis = by_wear, "wear"

    remaining = min(max(remaining, 0.0), config.LIFE_CAP_DAYS * 24.0)
    days = remaining / config.LIFE_HOURS_PER_DAY
    rough = bool((drive.get("raw") or {}).get("power_on_estimated"))
    return LifeEstimate(remaining, days, life_words(days), basis, rough)
