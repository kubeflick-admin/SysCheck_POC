"""Layer B: Windows' own drive reliability counters.

``MSFT_PhysicalDisk`` (namespace ``root\\Microsoft\\Windows\\Storage``)
describes each drive, and its associated ``MSFT_StorageReliabilityCounter``
carries the wear, temperature, power-on hours and error counts the storage
driver collected. It covers NVMe drives the old ATA SMART classes can't,
but the counters usually need Administrator.

Many drivers report 0 for counters they don't support, so readings where 0
can't be real (temperature) or can't be told apart from "unsupported"
(wear on a non-NVMe drive) are treated as unknown.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..wmi_util import wmi_connection
from .schema import (FLAG_FAILING, FLAG_OK, FLAG_UNKNOWN, FLAG_WARNING,
                     KIND_HDD, KIND_NVME, KIND_SATA_SSD, KIND_UNKNOWN,
                     SOURCE_RELIABILITY, as_int, new_drive, size_gb)

LogFn = Callable[[str], None]

DISK_FIELDS = ("DeviceId", "FriendlyName", "Model", "SerialNumber", "Size",
               "MediaType", "BusType", "HealthStatus", "FirmwareVersion",
               "SpindleSpeed")
COUNTER_FIELDS = ("Wear", "Temperature", "TemperatureMax", "PowerOnHours",
                  "ReadErrorsUncorrected", "WriteErrorsUncorrected",
                  "ReadErrorsTotal", "StartStopCycleCount")

MEDIA_HDD = 3
MEDIA_SSD = 4
BUS_ATA = 3
BUS_SATA = 11
BUS_NVME = 17
BUS_NAMES = {BUS_ATA: "ATA", 7: "USB", 8: "RAID", BUS_SATA: "SATA",
             12: "SD", 13: "MMC", BUS_NVME: "NVMe", 19: "UFS"}
SPINDLE_UNKNOWN = 0xFFFFFFFF
HEALTH_FLAGS = {0: FLAG_OK, 1: FLAG_WARNING, 2: FLAG_FAILING}


def kind_of(media_type: object, bus_type: object) -> str:
    media, bus = as_int(media_type), as_int(bus_type)
    if bus == BUS_NVME:
        return KIND_NVME
    if media == MEDIA_HDD:
        return KIND_HDD
    if media == MEDIA_SSD and bus in (BUS_ATA, BUS_SATA):
        return KIND_SATA_SSD
    return KIND_UNKNOWN


def health_flag(status: object) -> str:
    """MSFT_PhysicalDisk.HealthStatus (0 Healthy, 1 Warning, 2 Unhealthy)."""
    return HEALTH_FLAGS.get(as_int(status), FLAG_UNKNOWN)


def identity(row: Dict[str, object]) -> Dict[str, object]:
    """Model, serial and size of an MSFT_PhysicalDisk row."""
    model = str(row.get("Model") or row.get("FriendlyName") or "").strip()
    serial = str(row.get("SerialNumber") or "").strip()
    return {"model": model or None, "serial": serial or None,
            "size_gb": size_gb(row.get("Size"))}


def fetch(log: LogFn) -> List[Dict[str, object]]:
    """One row per MSFT_PhysicalDisk: its own fields plus its reliability
    counter's (all None when the counter can't be read)."""
    rows: List[Dict[str, object]] = []
    with wmi_connection("root\\Microsoft\\Windows\\Storage") as c:
        if c is None:
            return rows
        try:
            disks = c.MSFT_PhysicalDisk()
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] MSFT_PhysicalDisk unavailable: {exc}")
            return rows
        for disk in disks:
            row = {f: getattr(disk, f, None) for f in DISK_FIELDS}
            row.update(dict.fromkeys(COUNTER_FIELDS))
            try:
                counters = disk.associators(
                    wmi_result_class="MSFT_StorageReliabilityCounter")
            except Exception as exc:  # noqa: BLE001
                log(f"[DISK] reliability counter n/a for disk "
                    f"{row['DeviceId']} (elevation?): {exc}")
                counters = []
            if counters:
                row.update({f: getattr(counters[0], f, None)
                            for f in COUNTER_FIELDS})
            rows.append(row)
    return rows


def to_drive(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    """Normalised drive from a :func:`fetch` row, or None without counters."""
    if all(row.get(f) is None for f in COUNTER_FIELDS):
        return None

    kind = kind_of(row.get("MediaType"), row.get("BusType"))
    wear = as_int(row.get("Wear"))
    if kind == KIND_HDD or (wear == 0 and kind != KIND_NVME):
        wear = None
    read_bad = as_int(row.get("ReadErrorsUncorrected"))
    write_bad = as_int(row.get("WriteErrorsUncorrected"))
    uncorrectable = (None if read_bad is None and write_bad is None
                     else (read_bad or 0) + (write_bad or 0))

    drive = new_drive(
        **identity(row),
        kind=kind,
        source=SOURCE_RELIABILITY,
        health_flag=health_flag(row.get("HealthStatus")),
        power_on_hours=as_int(row.get("PowerOnHours")),
        temperature_c=as_int(row.get("Temperature")) or None,
        temperature_max_c=as_int(row.get("TemperatureMax")) or None,
        uncorrectable_errors=uncorrectable,
        percentage_used=wear,
    )
    spindle = as_int(row.get("SpindleSpeed"))
    drive["raw"] = {
        "interface": BUS_NAMES.get(as_int(row.get("BusType"))),
        "firmware": str(row.get("FirmwareVersion") or "").strip() or None,
        "disk_number": as_int(row.get("DeviceId")),
        "read_errors_total": as_int(row.get("ReadErrorsTotal")),
        "start_stop_cycles": as_int(row.get("StartStopCycleCount")),
    }
    if spindle and spindle != SPINDLE_UNKNOWN:
        drive["raw"]["rotation_rpm"] = spindle
    return drive
