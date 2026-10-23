"""Storage / S.M.A.R.T. collector.

Windows exposes raw S.M.A.R.T. attribute bytes through the WMI classes in
the ``root\\wmi`` namespace:

    MSStorageDriver_FailurePredictData    -> 512-byte vendor blob (attributes)
    MSStorageDriver_FailurePredictStatus  -> boolean PredictFailure flag

The vendor blob follows the ATA layout: a 2-byte header followed by up to
30 twelve-byte attribute records. We decode the two attributes a buyer
actually cares about:

    0x09 (9)   Power-On Hours
    0xF1 (241) Total LBAs Written -> Total Host Writes

Health status is taken from the FailurePredict flag, enriched by
``MSFT_PhysicalDisk.HealthStatus`` where available. Reading SMART usually
requires the app to run elevated; when it doesn't, we degrade to drive
model + capacity only and say so, rather than failing the scan.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .wmi_util import wmi_connection

LogFn = Callable[[str], None]

ATTR_POWER_ON_HOURS = 0x09
ATTR_TOTAL_LBA_WRITTEN = 0xF1
BYTES_PER_LBA = 512


def _noop(_: str) -> None:
    pass


def _parse_smart_blob(blob: bytes) -> Dict[int, int]:
    """Decode an ATA S.M.A.R.T. vendor blob into {attribute_id: raw_value}."""
    attrs: Dict[int, int] = {}
    # Records start at offset 2; each is 12 bytes wide.
    for off in range(2, min(len(blob), 2 + 30 * 12), 12):
        attr_id = blob[off]
        if attr_id == 0:
            continue
        # Raw value is a 6-byte little-endian integer at offset+5.
        raw = int.from_bytes(blob[off + 5:off + 11], "little")
        attrs[attr_id] = raw
    return attrs


def _normalise_instance(name: Optional[str]) -> str:
    return (name or "").strip().upper()


def _smart_entries(log: LogFn) -> Dict[str, Dict[str, object]]:
    """Return {instance_name: {power_on_hours, host_writes_tb}}."""
    out: Dict[str, Dict[str, object]] = {}
    with wmi_connection("root\\wmi") as c:
        if c is None:
            return out
        try:
            for item in c.MSStorageDriver_FailurePredictData():
                blob = bytes(item.VendorSpecific)
                attrs = _parse_smart_blob(blob)
                poh = attrs.get(ATTR_POWER_ON_HOURS)
                lbaw = attrs.get(ATTR_TOTAL_LBA_WRITTEN)
                host_tb = None
                if lbaw:
                    host_tb = round(lbaw * BYTES_PER_LBA / (1000 ** 4), 2)
                out[_normalise_instance(item.InstanceName)] = {
                    "power_on_hours": poh,
                    "host_writes_tb": host_tb,
                }
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] SMART data unavailable (elevation?): {exc}")
    return out


def _predict_failures(log: LogFn) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    with wmi_connection("root\\wmi") as c:
        if c is None:
            return out
        try:
            for item in c.MSStorageDriver_FailurePredictStatus():
                out[_normalise_instance(item.InstanceName)] = bool(item.PredictFailure)
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] SMART predict-status unavailable: {exc}")
    return out


def _physical_health(log: LogFn) -> List[str]:
    """MSFT_PhysicalDisk HealthStatus strings (0=Healthy,1=Warning,2=Unhealthy)."""
    mapping = {0: "Healthy", 1: "Warning", 2: "Unhealthy"}
    statuses: List[str] = []
    with wmi_connection("root\\Microsoft\\Windows\\Storage") as c:
        if c is None:
            return statuses
        try:
            for disk in c.MSFT_PhysicalDisk():
                code = getattr(disk, "HealthStatus", None)
                statuses.append(mapping.get(int(code), "Unknown") if code is not None
                                else "Unknown")
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] MSFT_PhysicalDisk unavailable: {exc}")
    return statuses


def _disk_models(log: LogFn) -> List[Dict[str, object]]:
    disks: List[Dict[str, object]] = []
    with wmi_connection() as c:
        if c is None:
            return disks
        try:
            for d in c.Win32_DiskDrive():
                size_gb = None
                if d.Size:
                    try:
                        size_gb = round(int(d.Size) / (1000 ** 3), 1)
                    except Exception:  # noqa: BLE001
                        size_gb = None
                disks.append({
                    "model": (d.Model or "Unknown drive").strip(),
                    "size_gb": size_gb,
                    "pnp": _normalise_instance(d.PNPDeviceID),
                    "media": (d.MediaType or "").strip() or None,
                })
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] Win32_DiskDrive failed: {exc}")
    return disks


def collect_storage_health(log: LogFn | None = None) -> Dict[str, object]:
    """Collect per-drive SMART health. Correlation across namespaces is
    best-effort; a single-SSD laptop (the common case) maps cleanly."""
    log = log or _noop

    log("[DISK] Enumerating physical drives ...")
    disks = _disk_models(log)
    log(f"[DISK]   -> {len(disks)} drive(s) detected")

    log("[DISK] Querying S.M.A.R.T. attribute tables ...")
    smart = _smart_entries(log)
    predict = _predict_failures(log)
    phys_health = _physical_health(log)

    drives: List[Dict[str, object]] = []
    for idx, disk in enumerate(disks):
        # Best-effort match: SMART InstanceName often contains the PNP id.
        match_key = next(
            (k for k in smart if disk["pnp"] and disk["pnp"][:20] in k),
            None,
        )
        if match_key is None and len(smart) == 1 and len(disks) == 1:
            match_key = next(iter(smart))  # single-drive fast path

        poh = host_tb = None
        if match_key:
            poh = smart[match_key].get("power_on_hours")
            host_tb = smart[match_key].get("host_writes_tb")

        predicted_fail = predict.get(match_key, None) if match_key else None
        health = "Unknown"
        if predicted_fail is True:
            health = "FAILING"
        elif predicted_fail is False:
            health = "OK"
        elif idx < len(phys_health):
            health = phys_health[idx]

        drive = {
            "model": disk["model"],
            "size_gb": disk["size_gb"],
            "media": disk["media"],
            "power_on_hours": poh,
            "host_writes_tb": host_tb,
            "health": health,
        }
        drives.append(drive)
        log(f"[DISK]   -> {disk['model']}: health={health}, "
            f"POH={poh if poh is not None else 'n/a'}h, "
            f"writes={host_tb if host_tb is not None else 'n/a'}TB")

    smart_readable = bool(smart)
    if not smart_readable:
        log("[DISK] NOTE: SMART attributes not readable "
            "(run elevated for power-on hours / host writes).")

    return {"drives": drives, "smart_readable": smart_readable}
