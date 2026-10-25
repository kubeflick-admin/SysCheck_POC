"""Plain-English wording and display formatting for drives.

The telemetry card, the storage page, the verdict card and the exported
report all describe drives through these helpers, so they always say the
same thing. Pure: no Qt, no WMI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .. import config
from ..telemetry.smart_sources.schema import (KIND_HDD, KIND_NVME,
                                              KIND_SATA_SSD, SOURCE_NONE,
                                              SOURCE_RELIABILITY,
                                              SOURCE_SMARTCTL,
                                              SOURCE_WMI_SMART, as_int)
from .health import (HealthResult, LifeEstimate, attribute_count, attributes,
                     compute_health, estimate_remaining_life, penalised,
                     threshold_crossed)

SOURCE_LABELS = {
    SOURCE_SMARTCTL: "smartctl (S.M.A.R.T.)",
    SOURCE_RELIABILITY: "Windows reliability counters",
    SOURCE_WMI_SMART: "Windows S.M.A.R.T. tables",
    SOURCE_NONE: "not readable",
}

HIDDEN = "Hidden"
NA = "n/a"

# Opening sentence for each condition word in config.HEALTH_WORDS.
_HEADLINES = {
    "EXCELLENT": "The {noun} is in good condition.",
    "GOOD": "The {noun} is in good condition.",
    "FAIR": ("The {noun} shows wear or problems. Keep backups and check it "
             "again soon."),
    "POOR": ("The {noun} has serious problems. Back up your data and plan to "
             "replace it."),
    "CRITICAL": ("The {noun} is failing or close to it. Back up your data now "
                 "and replace it."),
}


@dataclass
class DriveAssessment:
    """A drive with its Health % and remaining-life estimate."""

    drive: Dict[str, object]
    health: HealthResult
    life: LifeEstimate

    @property
    def name(self) -> str:
        return str(self.drive.get("model") or "Unknown drive")

    @property
    def headline(self) -> str:
        return headline(self.drive, self.health)

    @property
    def summary(self) -> str:
        return summary(self.drive, self.health)


def assess(drive: Dict[str, object]) -> DriveAssessment:
    health = compute_health(drive)
    return DriveAssessment(drive, health,
                           estimate_remaining_life(drive, health.health_pct))


# ─────────────────────────────────────────────────────────────────────
# Sentences
# ─────────────────────────────────────────────────────────────────────
def kind_noun(kind: object) -> str:
    if kind in (KIND_NVME, KIND_SATA_SSD):
        return "SSD"
    if kind == KIND_HDD:
        return "hard drive"
    return "drive"


def condition(health_pct: Optional[int]) -> str:
    """EXCELLENT / GOOD / FAIR / POOR / CRITICAL, or UNKNOWN."""
    if health_pct is None:
        return "UNKNOWN"
    return next(word for floor, word in config.HEALTH_WORDS
                if health_pct >= floor)


def headline(drive: Dict[str, object], health: HealthResult) -> str:
    """One sentence on the drive's overall condition."""
    noun = kind_noun(drive.get("kind"))
    pct = health.health_pct
    if pct is None:
        return (f"The {noun}'s health could not be read. Run SysCheck as "
                "Administrator to check it.")
    word = condition(pct)
    if word == "EXCELLENT" and all(r.key == "wear" for r in health.reasons):
        return f"The {noun} is in perfect condition. No problems found."
    return _HEADLINES[word].format(noun=noun)


def summary(drive: Dict[str, object], health: HealthResult,
            limit: int = 3) -> str:
    """The headline plus the top ``limit`` reasons, worst first."""
    parts = [headline(drive, health)]
    parts += [r.text for r in health.reasons[:limit]]
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Values
# ─────────────────────────────────────────────────────────────────────
def size_words(size_gb: object) -> str:
    if not isinstance(size_gb, (int, float)) or size_gb <= 0:
        return NA
    if size_gb >= 1000:
        return f"{round(size_gb / 1000, 1):g} TB"
    return f"{size_gb:.0f} GB"


def data_words(tb: object) -> str:
    if not isinstance(tb, (int, float)):
        return NA
    if tb < 1:
        return f"{tb * 1000:.0f} GB"
    return f"{tb:,.1f} TB"


def on_time_words(hours: object) -> str:
    """Power-on hours -> "about 2.3 years switched on"."""
    hours = as_int(hours)
    if hours is None:
        return NA
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} switched on"
    if hours < 24 * 60:
        return f"about {round(hours / 24)} days switched on"
    if hours < 8766:
        return f"about {round(hours / 730.5)} months switched on"
    return f"about {hours / 8766:.1f} years switched on"


def temperature_words(now: object, peak: object) -> str:
    now, peak = as_int(now), as_int(peak)
    if now is None and peak is None:
        return NA
    if peak is None:
        return f"{now} °C now · max not reported"
    if now is None:
        return f"{peak} °C max"
    return f"{now} °C now · {peak} °C max"


# Shown under a drive's details whenever it reports the count.
UNSAFE_SHUTDOWNS_TEXT = (
    "Unsafe shutdowns are times the drive lost power without warning (a flat "
    "battery, a crash or holding the power button); they rarely cause damage "
    "and don't lower Health %.")


def unsafe_shutdowns_note(drive: Dict[str, object]) -> Optional[str]:
    """What the unsafe-shutdown count means, or None without a count."""
    if as_int(drive.get("unsafe_shutdowns")) is None:
        return None
    return UNSAFE_SHUTDOWNS_TEXT


def _count(value: object) -> str:
    n = as_int(value)
    return f"{n:,}" if n is not None else NA


def _self_test_words(test: object) -> Optional[str]:
    if not isinstance(test, dict):
        return None
    status = test.get("status") or "unknown result"
    kind = test.get("type")
    return f"{kind}: {status}" if kind else str(status)


def detail_rows(drive: Dict[str, object],
                hide_serial: bool = False) -> List[Tuple[str, str]]:
    """(label, value) pairs describing a drive, for grids and reports."""
    raw = drive.get("raw") or {}
    poh = as_int(drive.get("power_on_hours"))
    on_time = NA
    if poh is not None:
        on_time = f"{poh:,} h — {on_time_words(poh)}"
        if raw.get("power_on_estimated"):
            on_time += " (estimated)"
    serial = drive.get("serial") or NA
    rows = [
        ("Model", str(drive.get("model") or "Unknown drive")),
        ("Serial", HIDDEN if hide_serial else str(serial)),
        ("Firmware", str(raw.get("firmware") or NA)),
        ("Type", str(drive.get("kind") or NA)),
        ("Interface", str(raw.get("interface") or NA)),
        ("Size", size_words(drive.get("size_gb"))),
        ("Temperature", temperature_words(drive.get("temperature_c"),
                                          drive.get("temperature_max_c"))),
        ("Power-on time", on_time),
        ("Data written", data_words(drive.get("host_writes_tb"))),
        ("Data read", data_words(raw.get("host_reads_tb"))),
        ("Power cycles", _count(drive.get("power_cycles"))),
        ("Unsafe shutdowns", _count(drive.get("unsafe_shutdowns"))),
    ]
    rpm = as_int(raw.get("rotation_rpm"))
    if rpm:
        rows.append(("Rotation speed", f"{rpm:,} rpm"))
    test = _self_test_words(drive.get("self_test"))
    if test:
        rows.append(("Last self-test", test))
    rows.append(("Read by", SOURCE_LABELS.get(drive.get("source"),
                                              str(drive.get("source")))))
    return rows


# ─────────────────────────────────────────────────────────────────────
# S.M.A.R.T. attribute table
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SmartRow:
    id: str
    name: str
    value: str
    worst: str
    thresh: str
    raw: str
    status: str
    severity: str           # ok | info | warn | fail

    def cells(self, columns: Tuple[str, ...]) -> Tuple[str, ...]:
        """This row's text under each of ``columns``."""
        return tuple(getattr(self, _COLUMN_FIELDS[c]) for c in columns)


ATA_COLUMNS = ("ID", "ATTRIBUTE", "VALUE", "WORST", "THRESH", "RAW", "STATUS")
# NVMe health-log fields are plain values, with no normalised value, worst
# or threshold, so their table is just the name, value and status.
NVME_COLUMNS = ("NAME", "VALUE", "STATUS")
_COLUMN_FIELDS = {"ID": "id", "ATTRIBUTE": "name", "NAME": "name",
                  "VALUE": "value", "WORST": "worst", "THRESH": "thresh",
                  "RAW": "raw", "STATUS": "status"}


# NVMe SMART / Health log fields, in log order, numbered the way common
# disk tools label them.
NVME_LOG_FIELDS = (
    ("critical_warning", "Critical warning"),
    ("temperature", "Temperature"),
    ("available_spare", "Available spare"),
    ("available_spare_threshold", "Available spare threshold"),
    ("percentage_used", "Percentage used"),
    ("data_units_read", "Data units read"),
    ("data_units_written", "Data units written"),
    ("host_reads", "Host read commands"),
    ("host_writes", "Host write commands"),
    ("controller_busy_time", "Controller busy time (min)"),
    ("power_cycles", "Power cycles"),
    ("power_on_hours", "Power-on hours"),
    ("unsafe_shutdowns", "Unsafe shutdowns"),
    ("media_errors", "Media and data integrity errors"),
    ("num_err_log_entries", "Error log entries"),
    ("warning_temp_time", "Warning temperature time (min)"),
    ("critical_comp_time", "Critical temperature time (min)"),
)
NVME_DATA_UNIT_TB = 512_000 / 1000 ** 4


def _num(value: object) -> str:
    return str(value) if isinstance(value, int) else "—"


def _ata_status(attr: Dict[str, object], kind: object) -> Tuple[str, str]:
    attr_id = attr.get("id")
    if threshold_crossed(attr):
        if attr_id in config.HEALTH_THRESHOLD_IGNORE_IDS:
            return "Too hot", "warn"
        return "FAILING", "fail"
    if attr.get("when_failed") == "past":
        return "Failed before", "warn"
    count = attribute_count(attr)
    if attr_id == 199 and count:
        return "Check cable", "info"
    if penalised(attr_id, kind) and count:
        return "Warning", "warn"
    return "OK", "ok"


def _ata_rows(drive: Dict[str, object]) -> List[SmartRow]:
    rows = []
    for attr in attributes(drive):
        raw = attr.get("raw_string")
        if not raw:
            raw = f"{attr['raw']:,}" if isinstance(attr.get("raw"), int) else "—"
        status, severity = _ata_status(attr, drive.get("kind"))
        rows.append(SmartRow(
            id=_num(attr.get("id")),
            name=str(attr.get("name") or "Unknown").replace("_", " "),
            value=_num(attr.get("value")), worst=_num(attr.get("worst")),
            thresh=_num(attr.get("thresh")), raw=str(raw),
            status=status, severity=severity))
    return rows


def _nvme_status(key: str, value: int, log: Dict[str, object]
                 ) -> Tuple[str, str]:
    if key == "critical_warning" and value:
        return "FAILING", "fail"
    if key == "available_spare":
        floor = as_int(log.get("available_spare_threshold"))
        if floor is not None and value < floor:
            return "FAILING", "fail"
    if key == "percentage_used":
        if value >= 100:
            return "Worn out", "fail"
        if value >= config.SSD_WEAR_WARN_PCT:
            return "Warning", "warn"
    if key == "media_errors" and value:
        return "Warning", "warn"
    return "OK", "ok"


def _nvme_value(key: str, value: int) -> str:
    if key == "critical_warning":
        return f"0x{value:02X}"
    if key == "temperature":
        return f"{value} °C"
    if key in ("available_spare", "available_spare_threshold",
               "percentage_used"):
        return f"{value}%"
    if key.startswith("data_units_"):
        return f"{value:,} ({data_words(round(value * NVME_DATA_UNIT_TB, 2))})"
    return f"{value:,}"


def _nvme_rows(log: Dict[str, object]) -> List[SmartRow]:
    rows = []
    for number, (key, name) in enumerate(NVME_LOG_FIELDS, start=1):
        value = as_int(log.get(key))
        if value is None:
            continue
        status, severity = _nvme_status(key, value, log)
        rows.append(SmartRow(f"{number:02X}", name, _nvme_value(key, value),
                             "—", "—", "—", status, severity))
    return rows


def smart_columns(drive: Dict[str, object]) -> Tuple[str, ...]:
    """The headings of the drive's :func:`smart_rows` table."""
    return ATA_COLUMNS if attributes(drive) else NVME_COLUMNS


def smart_rows(drive: Dict[str, object]) -> List[SmartRow]:
    """The drive's S.M.A.R.T. table: ATA attributes or the NVMe health
    log, with a status per row. [] when the source has no table."""
    if attributes(drive):
        return _ata_rows(drive)
    log = (drive.get("raw") or {}).get("nvme_log")
    return _nvme_rows(log) if isinstance(log, dict) else []


def no_table_reason(drive: Dict[str, object]) -> str:
    """Why :func:`smart_rows` came back empty, in plain English."""
    source = drive.get("source")
    if source == SOURCE_RELIABILITY:
        return ("Windows' reliability counters don't include a S.M.A.R.T. "
                "attribute table. Run SysCheck as Administrator so it can "
                "read the full table.")
    if source == SOURCE_NONE:
        return ("S.M.A.R.T. data could not be read. Run SysCheck as "
                "Administrator to read it.")
    return "The drive did not return a S.M.A.R.T. attribute table."
