"""Normalised per-drive record shared by every S.M.A.R.T. source.

Every field a source can't read stays ``None`` (never 0), so "no data" and
"a healthy zero" are always told apart downstream.

    model, serial, size_gb       identity (size in decimal GB)
    kind                         KIND_*
    source                       SOURCE_* (which layer produced the data)
    health_flag                  FLAG_* (the drive's own pass/fail verdict)
    power_on_hours, power_cycles, unsafe_shutdowns
    temperature_c, temperature_max_c
    reallocated_sectors          ATA 5
    pending_sectors              ATA 197
    uncorrectable_errors         ATA 198 (else 187); Windows uncorrected
                                 read + write errors
    crc_errors                   ATA 199 (cable / connector, not the media)
    media_errors                 NVMe media and data integrity errors
    error_log_entries            NVMe error log / ATA error log count
    percentage_used              NVMe percentage used, or SSD wear attribute
    available_spare_pct, available_spare_threshold_pct   NVMe only
    host_writes_tb               decimal TB; None when the units are unknown
    rated_tbw                    endurance rating, if known
    self_test                    most recent self-test result, or None
    raw                          anything extra (attribute table, firmware,
                                 interface, ...) for the report
"""

from __future__ import annotations

import re
from typing import Dict, Optional

KIND_NVME = "NVMe SSD"
KIND_SATA_SSD = "SATA SSD"
KIND_HDD = "HDD"
KIND_UNKNOWN = "Unknown"

SOURCE_SMARTCTL = "smartctl"
SOURCE_RELIABILITY = "reliability_counter"
SOURCE_WMI_SMART = "wmi_smart"
SOURCE_NONE = "none"

FLAG_OK = "OK"
FLAG_WARNING = "Warning"
FLAG_FAILING = "FAILING"
FLAG_UNKNOWN = "Unknown"

FIELDS = (
    "model", "serial", "size_gb", "kind", "source", "health_flag",
    "power_on_hours", "power_cycles", "unsafe_shutdowns",
    "temperature_c", "temperature_max_c",
    "reallocated_sectors", "pending_sectors", "uncorrectable_errors",
    "crc_errors", "media_errors", "error_log_entries",
    "percentage_used", "available_spare_pct", "available_spare_threshold_pct",
    "host_writes_tb", "rated_tbw",
    "self_test", "raw",
)


def new_drive(**values: object) -> Dict[str, object]:
    """A drive record with every field unknown, then ``values`` applied."""
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise KeyError(f"not drive fields: {sorted(unknown)}")
    drive: Dict[str, object] = dict.fromkeys(FIELDS)
    drive.update(kind=KIND_UNKNOWN, source=SOURCE_NONE,
                 health_flag=FLAG_UNKNOWN, raw={})
    drive.update(values)
    return drive


def as_int(value: object) -> Optional[int]:
    """Best-effort int: WMI hands back uint64 values as strings."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def size_gb(size_bytes: object) -> Optional[float]:
    n = as_int(size_bytes)
    return round(n / (1000 ** 3), 1) if n else None


def normalise_serial(serial: object) -> str:
    """Upper-case serial with spaces, ``_``, ``.`` and ``-`` removed.

    Windows and smartctl format the same serial differently (padding,
    NVMe underscores and trailing dots), so compare this form only.
    """
    return re.sub(r"[\s_.\-]", "", str(serial or "")).upper()


def normalise_model(model: object) -> str:
    """Upper-case model with whitespace collapsed and Windows' bus suffix
    (" ATA Device", " SCSI Disk Device", ...) removed."""
    text = " ".join(str(model or "").split()).upper()
    return re.sub(r"\s+(ATA|SCSI DISK|USB) DEVICE$", "", text)
