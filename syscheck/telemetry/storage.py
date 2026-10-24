"""Storage / S.M.A.R.T. collector.

Lists the physical drives (``Win32_DiskDrive`` joined to
``MSFT_PhysicalDisk`` on the disk number), then for each drive keeps the
first S.M.A.R.T. layer that can read it:

    A  smartctl (bundled smartmontools)           smart_sources/smartctl.py
    B  MSFT_StorageReliabilityCounter              smart_sources/reliability.py
    C  MSStorageDriver_FailurePredict* ATA blobs   smart_sources/wmi_smart.py

Counters are never mixed across layers, because their units can differ.
Identity (model, serial, size, kind) fills in from the Windows inventory
wherever the winning layer left it blank, and a drive no layer can read is
still listed with ``source="none"`` and Windows' own health status, rather
than failing the scan.

Each drive is the normalised dict from ``smart_sources.schema`` plus
``health`` (a copy of ``health_flag``), which the current score and
telemetry card read.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .smart_sources import matching, reliability, smartctl, wmi_smart
from .smart_sources.schema import (FLAG_UNKNOWN, KIND_UNKNOWN, SOURCE_NONE,
                                   as_int, new_drive, normalise_serial,
                                   size_gb)
from .wmi_util import wmi_connection

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _safe(fn: Callable, default: object, log: LogFn) -> object:
    """Run one source; a crash in it must never take the scan down."""
    try:
        return fn(log)
    except Exception as exc:  # noqa: BLE001
        log(f"[WARN] {fn.__module__}.{fn.__name__} failed: {exc}")
        return default


def _inventory(log: LogFn) -> List[Dict[str, object]]:
    """Physical drives with identity, kind, Windows health and the
    MSFT_PhysicalDisk row (reliability counters included) for each."""
    rows: Dict[int, Dict[str, object]] = {}
    for row in reliability.fetch(log):
        number = as_int(row.get("DeviceId"))
        if number is not None:
            rows[number] = row

    disks: List[Dict[str, object]] = []
    with wmi_connection() as c:
        if c is not None:
            try:
                for d in c.Win32_DiskDrive():
                    disks.append({
                        "disk_number": as_int(d.Index),
                        "model": (d.Model or "Unknown drive").strip(),
                        "serial": (d.SerialNumber or "").strip() or None,
                        "size_gb": size_gb(d.Size),
                        "pnp": wmi_smart.instance_key(d.PNPDeviceID) or None,
                    })
            except Exception as exc:  # noqa: BLE001
                log(f"[WARN] Win32_DiskDrive failed: {exc}")

    listed = {d["disk_number"] for d in disks}
    for number in sorted(rows.keys() - listed):
        disks.append({"disk_number": number, "model": "Unknown drive",
                      "serial": None, "size_gb": None, "pnp": None})

    for d in disks:
        row = rows.get(d["disk_number"])
        d["row"] = row
        d["kind"] = KIND_UNKNOWN
        d["windows_health"] = FLAG_UNKNOWN
        if row:
            # MSFT_PhysicalDisk reports the exact size; Win32_DiskDrive
            # rounds it down to whole cylinders.
            d.update({k: v for k, v in reliability.identity(row).items()
                      if v is not None})
            d["kind"] = reliability.kind_of(row.get("MediaType"),
                                            row.get("BusType"))
            d["windows_health"] = reliability.health_flag(
                row.get("HealthStatus"))
    disks.sort(key=lambda d: (d["disk_number"] is None, d["disk_number"]))
    return disks


def _identity(drive: Dict[str, object]) -> Dict[str, object]:
    return {"serial": drive["serial"], "model": drive["model"],
            "size_gb": drive["size_gb"],
            "disk_number": drive["raw"].get("disk_number")}


def _take_smartctl(disk: Dict[str, object],
                   pending: List[Dict[str, object]]
                   ) -> Optional[Dict[str, object]]:
    idx = matching.find_match(disk, [_identity(p) for p in pending])
    return pending.pop(idx) if idx is not None else None


def _wmi_smart_drive(disk: Dict[str, object],
                     entries: Dict[str, Dict[str, object]],
                     only_disk: bool) -> Optional[Dict[str, object]]:
    key = next((k for k in entries if matching.pnp_matches(disk["pnp"], k)),
               None)
    if key is None and only_disk and len(entries) == 1:
        key = next(iter(entries))  # one disk, one SMART entry
    if key is None:
        return None
    entry = entries[key]
    return wmi_smart.to_drive(entry["attrs"], entry["thresholds"],
                              entry["predict_failure"])


def _finish(drive: Dict[str, object],
            disk: Optional[Dict[str, object]]) -> Dict[str, object]:
    """Fill identity gaps from the inventory and add the legacy key."""
    if disk is not None:
        for field in ("model", "serial", "size_gb"):
            if drive[field] is None:
                drive[field] = disk[field]
        if drive["kind"] == KIND_UNKNOWN:
            drive["kind"] = disk["kind"]
        if drive["health_flag"] == FLAG_UNKNOWN:
            drive["health_flag"] = disk["windows_health"]
        raw = drive["raw"]
        if raw.get("disk_number") is None:
            raw["disk_number"] = disk["disk_number"]
        raw["windows_health"] = disk["windows_health"]
        if (disk["serial"] and normalise_serial(disk["serial"])
                != normalise_serial(drive["serial"])):
            raw["windows_serial"] = disk["serial"]
    drive["health"] = drive["health_flag"]
    return drive


def _fmt(value: object, unit: str) -> str:
    return f"{value}{unit}" if value is not None else "n/a"


def collect_storage_health(log: LogFn | None = None) -> Dict[str, object]:
    """Collect per-drive S.M.A.R.T. health, layer by layer."""
    log = log or _noop

    log("[DISK] Enumerating physical drives ...")
    disks = _safe(_inventory, [], log)
    log(f"[DISK]   -> {len(disks)} drive(s) detected")

    log("[DISK] Reading S.M.A.R.T. via smartctl ...")
    pending = _safe(smartctl.collect, [], log)
    wmi_entries: Optional[Dict[str, Dict[str, object]]] = None

    drives: List[Dict[str, object]] = []
    for disk in disks:
        drive = _take_smartctl(disk, pending)
        if drive is None and disk["row"]:
            drive = reliability.to_drive(disk["row"])
        if drive is None and disk["pnp"]:
            if wmi_entries is None:  # only queried when a drive needs it
                log("[DISK] Querying ATA S.M.A.R.T. tables ...")
                wmi_entries = _safe(wmi_smart.fetch, {}, log)
            drive = _wmi_smart_drive(disk, wmi_entries, len(disks) == 1)
        drives.append(_finish(drive or new_drive(), disk))
    # Drives smartctl read but Windows didn't list.
    drives.extend(_finish(d, None) for d in pending)

    for d in drives:
        log(f"[DISK]   -> {d['model']} [{d['kind']}] via {d['source']}: "
            f"health={d['health_flag']}, "
            f"POH={_fmt(d['power_on_hours'], 'h')}, "
            f"writes={_fmt(d['host_writes_tb'], 'TB')}")

    smart_readable = any(d["source"] != SOURCE_NONE for d in drives)
    if not smart_readable:
        log("[DISK] NOTE: SMART data not readable "
            "(run elevated for power-on hours / wear / errors).")

    return {"drives": drives, "smart_readable": smart_readable}
