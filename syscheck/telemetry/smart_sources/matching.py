"""Recognise the same physical drive across sources.

Keys, strongest first:

1. Serial number. Not always usable: for NVMe drives Windows often reports
   a namespace ID (``xxxx_xxxx_..._xxxx.``) where smartctl has the real
   serial.
2. Windows disk number. smartctl's ``/dev/sdX`` / ``/dev/pdN`` names open
   ``\\\\.\\PhysicalDriveN`` directly, so they line up with
   ``Win32_DiskDrive.Index`` and ``MSFT_PhysicalDisk.DeviceId``.
3. Model + size (within ``config.DRIVE_SIZE_MATCH_TOLERANCE``), only when
   exactly one candidate fits, so two identical drives never get mixed up.

Layer C's WMI instance names embed the disk's PNPDeviceID, so those match
by prefix instead.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ... import config
from .schema import normalise_model, normalise_serial


def same_serial(a: object, b: object) -> bool:
    na, nb = normalise_serial(a), normalise_serial(b)
    return bool(na) and na == nb


def same_model_and_size(a: Dict[str, object], b: Dict[str, object]) -> bool:
    model = normalise_model(a.get("model"))
    if not model or model != normalise_model(b.get("model")):
        return False
    sa, sb = a.get("size_gb"), b.get("size_gb")
    if not sa or not sb:
        return False
    return abs(sa - sb) <= config.DRIVE_SIZE_MATCH_TOLERANCE * max(sa, sb)


def _same_disk_number(a: Dict[str, object], b: Dict[str, object]) -> bool:
    if a.get("disk_number") is None or a.get("disk_number") != b.get(
            "disk_number"):
        return False
    # Guard against a stale mapping: both models known but different.
    ma, mb = normalise_model(a.get("model")), normalise_model(b.get("model"))
    return not (ma and mb and ma != mb)


def find_match(target: Dict[str, object],
               candidates: List[Dict[str, object]]) -> Optional[int]:
    """Index of the candidate that is the same drive as ``target``.

    Both sides are identity dicts: serial, model, size_gb, disk_number.
    """
    for i, c in enumerate(candidates):
        if same_serial(target.get("serial"), c.get("serial")):
            return i
    for i, c in enumerate(candidates):
        if _same_disk_number(target, c):
            return i
    hits = [i for i, c in enumerate(candidates)
            if same_model_and_size(target, c)]
    return hits[0] if len(hits) == 1 else None


def pnp_matches(pnp_device_id: object, instance_name: object) -> bool:
    """True when a FailurePredict ``InstanceName`` belongs to the disk with
    this PNPDeviceID (the instance name is the full ID plus ``_N``)."""
    pnp = str(pnp_device_id or "").strip().upper()
    inst = str(instance_name or "").strip().upper()
    if not pnp or not inst.startswith(pnp):
        return False
    rest = inst[len(pnp):]
    return rest == "" or rest.startswith("_")
