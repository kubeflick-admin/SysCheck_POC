"""Layer A: smartmontools' ``smartctl`` with JSON output.

smartctl knows thousands of drives through its drive database, so it is
the only source that interprets vendor-specific raw values (power-on time
units, host-write units) instead of guessing. Only its JSON output is
parsed, never the text report.

The official Windows build ships in ``vendor/smartmontools`` and is
bundled into the PyInstaller .exe. NVMe drives are usually readable
without elevation; SATA drives need Administrator. A drive smartctl can't
read simply falls through to the next layer.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ... import config
from .schema import (FLAG_FAILING, FLAG_OK, FLAG_UNKNOWN, KIND_HDD, KIND_NVME,
                     KIND_SATA_SSD, KIND_UNKNOWN, SOURCE_SMARTCTL, as_int,
                     new_drive, size_gb)

LogFn = Callable[[str], None]

# Where smartctl.exe lives, relative to the repo root or the PyInstaller
# bundle (build.ps1 / SysCheck.spec put it at the same path).
VENDOR_DIR = ("vendor", "smartmontools")
EXE_NAME = "smartctl.exe"

# smartctl's exit status is a bitmask. Bit 0 (bad command line) and bit 1
# (device open / identify failed) mean there is no data; the other bits
# report findings (bit 3 "disk failing", ...) alongside valid JSON.
EXIT_NO_DATA = 0b11

NVME_DATA_UNIT_BYTES = 512_000          # NVMe "data units" are 1000 x 512 B

# Bytes per raw count of ATA attribute 241, keyed by smartctl's drive-
# database name for it (None = one logical block). Other names mean the
# vendor unit is unknown, and no figure is better than a wrong one.
HOST_WRITE_UNITS = {
    "Total_LBAs_Written": None,
    "Host_Writes_32MiB": 32 * 1024 ** 2,
    "Host_Writes_MiB": 1024 ** 2,
    "Host_Writes_GiB": 1024 ** 3,
    "Lifetime_Writes_GiB": 1024 ** 3,
}

# SATA SSD life-left attributes whose normalised value counts down from 100.
LIFE_LEFT_NAMES = ("Wear_Leveling_Count", "SSD_Life_Left",
                   "Media_Wearout_Indicator", "Percent_Lifetime_Remain")

UNSAFE_SHUTDOWN_NAMES = ("Unexpect_Power_Loss_Ct", "Unsafe_Shutdown_Count",
                         "Power-Off_Retract_Count")

_MIN_MAX_RE = re.compile(r"Min/Max\s+\d+/(\d+)")


# ─────────────────────────────────────────────────────────────────────
# Running smartctl
# ─────────────────────────────────────────────────────────────────────
def find_smartctl() -> Optional[Path]:
    """The bundled smartctl.exe (frozen build or source tree), or None."""
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parents[3]
    exe = root.joinpath(*VENDOR_DIR, EXE_NAME)
    return exe if exe.is_file() else None


def is_admin() -> bool:
    """True when the process is elevated; always False off Windows."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - no windll off Windows
        return False


def _run_json(exe: Path, args: List[str], log: LogFn) -> Optional[dict]:
    cmd = " ".join(args)
    try:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True, timeout=config.SMARTCTL_TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log(f"[DISK] smartctl {cmd} timed out")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"[DISK] smartctl {cmd} failed: {exc}")
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        log(f"[DISK] smartctl {cmd} gave no JSON (exit {proc.returncode})")
        return None


def collect(log: LogFn) -> List[Dict[str, object]]:
    """Every drive smartctl can read, or [] when it can't run here."""
    exe = find_smartctl()
    if exe is None:
        log("[DISK] smartctl.exe not found; skipping it.")
        return []

    hint = "" if is_admin() else " (run as Administrator?)"
    drives: List[Dict[str, object]] = []
    for dev in parse_scan(_run_json(exe, ["--scan-open", "-j"], log)):
        args = ["-a", "-j"]
        if dev["type"]:
            args += ["-d", dev["type"]]
        args.append(dev["name"])
        report = _run_json(exe, args, log)
        drive = parse_device(report)
        if drive is None:
            why = report_messages(report) if isinstance(report, dict) else []
            log(f"[DISK] smartctl could not read {dev['name']}"
                + (f": {why[0]}" if why else "") + hint)
            continue
        drives.append(drive)
    return drives


# ─────────────────────────────────────────────────────────────────────
# Parsing (pure)
# ─────────────────────────────────────────────────────────────────────
def report_usable(report: object) -> bool:
    """True when a smartctl JSON report carries device data."""
    if not isinstance(report, dict):
        return False
    status = (report.get("smartctl") or {}).get("exit_status")
    return isinstance(status, int) and not status & EXIT_NO_DATA


def report_messages(report: dict) -> List[str]:
    msgs = (report.get("smartctl") or {}).get("messages") or []
    return [m.get("string", "") for m in msgs if isinstance(m, dict)]


def parse_scan(report: object) -> List[Dict[str, Optional[str]]]:
    """``--scan-open -j`` -> [{name, type}] for devices that opened."""
    if not isinstance(report, dict):
        return []
    devices = []
    for dev in report.get("devices") or []:
        if not isinstance(dev, dict) or not dev.get("name"):
            continue
        if dev.get("open_error"):
            continue
        devices.append({"name": dev["name"], "type": dev.get("type")})
    return devices


def ata_host_writes_tb(name: Optional[str], raw: Optional[int],
                       block_size: Optional[int]) -> Optional[float]:
    """Host writes in TB from attribute 241, or None if its unit is unknown."""
    if raw is None or name not in HOST_WRITE_UNITS:
        return None
    unit = HOST_WRITE_UNITS[name] or (block_size or 512)
    return round(raw * unit / (1000 ** 4), 2)


def _nvme_tb(data_units: object) -> Optional[float]:
    units = as_int(data_units)
    if units is None:
        return None
    return round(units * NVME_DATA_UNIT_BYTES / (1000 ** 4), 2)


def _ata_attributes(report: dict) -> List[Dict[str, object]]:
    table = (report.get("ata_smart_attributes") or {}).get("table") or []
    attrs = []
    for a in table:
        if not isinstance(a, dict):
            continue
        raw = a.get("raw") or {}
        attrs.append({
            "id": a.get("id"),
            "name": a.get("name"),
            "value": a.get("value"),
            "worst": a.get("worst"),
            "thresh": a.get("thresh"),
            "raw": as_int(raw.get("value")),
            "raw_string": raw.get("string"),
            "when_failed": a.get("when_failed") or None,
            "prefailure": (a.get("flags") or {}).get("prefailure"),
        })
    return attrs


def _by_id(attrs: List[Dict[str, object]], attr_id: int) -> Optional[dict]:
    return next((a for a in attrs if a["id"] == attr_id), None)


def _raw(attrs: List[Dict[str, object]], attr_id: int) -> Optional[int]:
    a = _by_id(attrs, attr_id)
    return a["raw"] if a else None


def _kind(report: dict) -> str:
    protocol = (report.get("device") or {}).get("protocol")
    if protocol == "NVMe":
        return KIND_NVME
    if protocol == "ATA":
        rpm = report.get("rotation_rate")
        if rpm == 0:
            return KIND_SATA_SSD
        if isinstance(rpm, int) and rpm > 0:
            return KIND_HDD
    return KIND_UNKNOWN


def _health_flag(report: dict) -> str:
    nvme = report.get("nvme_smart_health_information_log") or {}
    if nvme.get("critical_warning"):
        return FLAG_FAILING
    passed = (report.get("smart_status") or {}).get("passed")
    if passed is True:
        return FLAG_OK
    if passed is False:
        return FLAG_FAILING
    return FLAG_UNKNOWN


def _self_test(report: dict) -> Optional[Dict[str, object]]:
    ata = ((report.get("ata_smart_self_test_log") or {})
           .get("standard") or {}).get("table") or []
    if ata:
        last = ata[0]
        status = last.get("status") or {}
        return {"type": (last.get("type") or {}).get("string"),
                "status": status.get("string"),
                "passed": status.get("passed"),
                "lifetime_hours": last.get("lifetime_hours"),
                "count": len(ata)}
    nvme = (report.get("nvme_self_test_log") or {}).get("table") or []
    if nvme:
        last = nvme[0]
        result = last.get("self_test_result") or {}
        return {"type": (last.get("self_test_code") or {}).get("string"),
                "status": result.get("string"),
                "passed": (result.get("value") == 0
                           if result.get("value") is not None else None),
                "lifetime_hours": last.get("power_on_hours"),
                "count": len(nvme)}
    return None


def _temperature_max(report: dict, attrs: List[Dict[str, object]]
                     ) -> Optional[int]:
    lifetime = as_int((report.get("temperature") or {}).get("lifetime_max"))
    if lifetime is not None:
        return lifetime
    for attr_id in (194, 190):
        a = _by_id(attrs, attr_id)
        match = _MIN_MAX_RE.search(str(a.get("raw_string") or "")) if a else None
        if match:
            return int(match.group(1))
    return None


def parse_device(report: object) -> Optional[Dict[str, object]]:
    """One ``smartctl -a -j`` report -> normalised drive, or None."""
    if not report_usable(report):
        return None

    kind = _kind(report)
    attrs = _ata_attributes(report)
    nvme = report.get("nvme_smart_health_information_log") or {}
    device = report.get("device") or {}
    protocol = device.get("protocol")
    temp = (report.get("temperature") or {}).get("current")
    if temp is None:
        for attr_id in (194, 190):
            raw = _raw(attrs, attr_id)
            if raw is not None:
                temp = raw & 0xFF
                break

    drive = new_drive(
        model=(report.get("model_name") or "").strip() or None,
        serial=(report.get("serial_number") or "").strip() or None,
        size_gb=size_gb((report.get("user_capacity") or {}).get("bytes")),
        kind=kind,
        source=SOURCE_SMARTCTL,
        health_flag=_health_flag(report),
        power_on_hours=as_int((report.get("power_on_time") or {}).get("hours")),
        power_cycles=as_int(report.get("power_cycle_count")),
        temperature_c=as_int(temp),
        temperature_max_c=_temperature_max(report, attrs),
        self_test=_self_test(report),
    )
    raw: Dict[str, object] = {
        "device": device.get("name"),
        "device_type": device.get("type"),
        "protocol": protocol,
        "firmware": report.get("firmware_version"),
        "model_family": report.get("model_family"),
        "exit_status": (report.get("smartctl") or {}).get("exit_status"),
        "messages": report_messages(report),
    }
    if "rotation_rate" in report:
        raw["rotation_rpm"] = report.get("rotation_rate")

    if protocol == "NVMe":
        raw["interface"] = "NVMe"
        raw["nvme_critical_warning"] = nvme.get("critical_warning")
        raw["host_reads_tb"] = _nvme_tb(nvme.get("data_units_read"))
        drive["host_writes_tb"] = _nvme_tb(nvme.get("data_units_written"))
        for field, key in (("power_on_hours", "power_on_hours"),
                           ("power_cycles", "power_cycles"),
                           ("temperature_c", "temperature")):
            if drive[field] is None:
                drive[field] = as_int(nvme.get(key))
        drive["unsafe_shutdowns"] = as_int(nvme.get("unsafe_shutdowns"))
        drive["media_errors"] = as_int(nvme.get("media_errors"))
        drive["error_log_entries"] = as_int(nvme.get("num_err_log_entries"))
        drive["percentage_used"] = as_int(nvme.get("percentage_used"))
        drive["available_spare_pct"] = as_int(nvme.get("available_spare"))
        drive["available_spare_threshold_pct"] = as_int(
            nvme.get("available_spare_threshold"))
    else:
        raw["interface"] = "SATA" if "sata_version" in report else protocol
        raw["attributes"] = attrs
        if drive["power_cycles"] is None:
            drive["power_cycles"] = _raw(attrs, 12)
        drive["reallocated_sectors"] = _raw(attrs, 5)
        drive["pending_sectors"] = _raw(attrs, 197)
        uncorrectable = _raw(attrs, 198)
        drive["uncorrectable_errors"] = (uncorrectable if uncorrectable
                                         is not None else _raw(attrs, 187))
        drive["crc_errors"] = _raw(attrs, 199)
        drive["unsafe_shutdowns"] = next(
            (a["raw"] for a in attrs if a["name"] in UNSAFE_SHUTDOWN_NAMES),
            None)
        drive["error_log_entries"] = as_int(
            ((report.get("ata_smart_error_log") or {}).get("summary") or {})
            .get("count"))
        writes = _by_id(attrs, 241)
        if writes:
            drive["host_writes_tb"] = ata_host_writes_tb(
                writes["name"], writes["raw"],
                as_int(report.get("logical_block_size")))
        if kind == KIND_SATA_SSD:
            used = [100 - a["value"] for a in attrs
                    if a["name"] in LIFE_LEFT_NAMES
                    and isinstance(a["value"], int) and a["value"] <= 100]
            if used:
                drive["percentage_used"] = max(used)

    # smartctl's own standardised wear figure (NVMe log, or ATA device
    # statistics) beats any vendor attribute when it is present.
    endurance = as_int((report.get("endurance_used") or {})
                       .get("current_percent"))
    if endurance is not None:
        drive["percentage_used"] = endurance

    drive["raw"] = raw
    return drive
