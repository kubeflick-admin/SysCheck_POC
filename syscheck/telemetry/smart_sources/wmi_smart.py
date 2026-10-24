"""Layer C: raw ATA S.M.A.R.T. blobs from the ``root\\wmi`` namespace.

    MSStorageDriver_FailurePredictData        512-byte attribute blob
    MSStorageDriver_FailurePredictThresholds  512-byte threshold blob
    MSStorageDriver_FailurePredictStatus      PredictFailure flag

Both blobs follow the ATA layout: a 2-byte header, then up to 30 records
of 12 bytes. Attribute record: id, flags (2, LE), normalised value, worst,
raw (6, LE), reserved. Threshold record: id, threshold, reserved (10).

This is the last resort for older SATA drives and has no drive database,
so only attributes with a standard meaning are mapped. Host writes (241)
stay in the attribute table but are never converted, because their unit
differs by vendor. Power-on hours assume the usual hours unit.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..wmi_util import wmi_connection
from .schema import (FLAG_FAILING, FLAG_OK, FLAG_UNKNOWN, SOURCE_WMI_SMART,
                     new_drive)

LogFn = Callable[[str], None]

HEADER_BYTES = 2
RECORD_BYTES = 12
MAX_RECORDS = 30

# Labels for attributes whose meaning is the same on every vendor. Others
# (231, 233, 241, 242, ...) differ by drive and stay unnamed here.
ATTR_NAMES = {
    1: "Raw_Read_Error_Rate", 3: "Spin_Up_Time", 4: "Start_Stop_Count",
    5: "Reallocated_Sector_Ct", 7: "Seek_Error_Rate", 9: "Power_On_Hours",
    10: "Spin_Retry_Count", 12: "Power_Cycle_Count",
    184: "End-to-End_Error", 187: "Reported_Uncorrect",
    188: "Command_Timeout", 189: "High_Fly_Writes",
    190: "Airflow_Temperature_Cel", 191: "G-Sense_Error_Rate",
    192: "Power-Off_Retract_Count", 193: "Load_Cycle_Count",
    194: "Temperature_Celsius", 196: "Reallocated_Event_Count",
    197: "Current_Pending_Sector", 198: "Offline_Uncorrectable",
    199: "UDMA_CRC_Error_Count",
}


def instance_key(name: object) -> str:
    return str(name or "").strip().upper()


def parse_smart_blob(blob: bytes) -> Dict[int, Dict[str, Optional[int]]]:
    """Attribute blob -> {id: {flags, value, worst, raw}}.

    A record cut short by a truncated blob keeps whatever bytes survive
    rather than raising.
    """
    attrs: Dict[int, Dict[str, Optional[int]]] = {}
    end = min(len(blob), HEADER_BYTES + MAX_RECORDS * RECORD_BYTES)
    for off in range(HEADER_BYTES, end, RECORD_BYTES):
        rec = blob[off:off + RECORD_BYTES]
        if rec[0] == 0:
            continue
        attrs[rec[0]] = {
            "flags": int.from_bytes(rec[1:3], "little") if len(rec) >= 3
            else None,
            "value": rec[3] if len(rec) > 3 else None,
            "worst": rec[4] if len(rec) > 4 else None,
            "raw": int.from_bytes(rec[5:11], "little"),
        }
    return attrs


def parse_threshold_blob(blob: bytes) -> Dict[int, int]:
    """Threshold blob -> {id: threshold}."""
    thresholds: Dict[int, int] = {}
    end = min(len(blob), HEADER_BYTES + MAX_RECORDS * RECORD_BYTES)
    for off in range(HEADER_BYTES, end, RECORD_BYTES):
        rec = blob[off:off + 2]
        if len(rec) == 2 and rec[0] != 0:
            thresholds[rec[0]] = rec[1]
    return thresholds


def _temperatures(raw: Optional[int]) -> tuple:
    """(current, lifetime max) from a 194/190 raw value.

    Byte 0 is the current temperature. Many drives keep min/max in bytes
    2 and 4 (in either order); a max is only reported when they bracket
    the current reading plausibly.
    """
    if raw is None:
        return None, None
    b = raw.to_bytes(6, "little")
    current = b[0] or None
    if current is None:
        return None, None
    for low, high in ((b[2], b[4]), (b[4], b[2])):
        if 0 < low <= current <= high < 128:
            return current, high
    return current, None


def _table(attrs: Dict[int, Dict[str, Optional[int]]],
           thresholds: Dict[int, int]) -> List[Dict[str, object]]:
    """Attribute list in the same shape as the smartctl parser's."""
    table = []
    for attr_id in sorted(attrs):
        a = attrs[attr_id]
        thresh = thresholds.get(attr_id)
        failing = (thresh is not None and thresh > 0
                   and a["value"] is not None and a["value"] <= thresh)
        table.append({
            "id": attr_id,
            "name": ATTR_NAMES.get(attr_id),
            "value": a["value"],
            "worst": a["worst"],
            "thresh": thresh,
            "raw": a["raw"],
            "raw_string": None,
            "when_failed": "now" if failing else None,
            "prefailure": (bool(a["flags"] & 1) if a["flags"] is not None
                           else None),
        })
    return table


def to_drive(attrs: Dict[int, Dict[str, Optional[int]]],
             thresholds: Dict[int, int],
             predict_failure: Optional[bool]) -> Optional[Dict[str, object]]:
    """Normalised drive from one disk's blobs, or None without attributes."""
    if not attrs:
        return None

    def raw(attr_id: int) -> Optional[int]:
        return attrs[attr_id]["raw"] if attr_id in attrs else None

    temp, temp_max = _temperatures(raw(194))
    if temp is None:
        temp, temp_max = _temperatures(raw(190))
    poh = raw(9)
    timeouts = raw(188)
    uncorrectable = raw(198)

    drive = new_drive(
        source=SOURCE_WMI_SMART,
        health_flag={True: FLAG_FAILING, False: FLAG_OK}.get(
            predict_failure, FLAG_UNKNOWN),
        # Some vendors pack minutes/ms above bit 32 of the hours counter.
        power_on_hours=poh & 0xFFFFFFFF if poh is not None else None,
        power_cycles=raw(12),
        temperature_c=temp,
        temperature_max_c=temp_max,
        reallocated_sectors=raw(5),
        pending_sectors=raw(197),
        uncorrectable_errors=(uncorrectable if uncorrectable is not None
                              else raw(187)),
        crc_errors=raw(199),
        unsafe_shutdowns=raw(192),
    )
    drive["raw"] = {
        "attributes": _table(attrs, thresholds),
        "power_on_hours_raw": poh,
        # Some vendors pack three 16-bit counters into 188.
        "command_timeouts": timeouts & 0xFFFF if timeouts is not None
        else None,
        "predict_failure": predict_failure,
    }
    return drive


def fetch(log: LogFn) -> Dict[str, Dict[str, object]]:
    """{InstanceName: {attrs, thresholds, predict_failure}} for every disk
    the ATA SMART classes report (usually needs Administrator)."""
    readers = (
        ("MSStorageDriver_FailurePredictData", "attrs",
         lambda item: parse_smart_blob(bytes(item.VendorSpecific))),
        ("MSStorageDriver_FailurePredictThresholds", "thresholds",
         lambda item: parse_threshold_blob(bytes(item.VendorSpecific))),
        ("MSStorageDriver_FailurePredictStatus", "predict_failure",
         lambda item: bool(item.PredictFailure)),
    )
    entries: Dict[str, Dict[str, object]] = {}
    with wmi_connection("root\\wmi") as c:
        if c is None:
            return entries
        for cls, key, read in readers:
            try:
                items = getattr(c, cls)()
            except Exception as exc:  # noqa: BLE001
                log(f"[WARN] {cls} unavailable (elevation?): {exc}")
                continue
            for item in items:
                entry = entries.setdefault(
                    instance_key(item.InstanceName),
                    {"attrs": {}, "thresholds": {}, "predict_failure": None})
                try:
                    entry[key] = read(item)
                except Exception as exc:  # noqa: BLE001
                    log(f"[WARN] {cls} unreadable: {exc}")
    return entries
