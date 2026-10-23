"""Core specification collector: CPU, RAM, GPU, motherboard, BIOS.

Returns a dict of display-ready strings/values. Everything is wrapped in
defensive try/except because second-hand machines report wildly
inconsistent WMI data, and we would rather show "Unknown" than crash the
scan.
"""

from __future__ import annotations

from typing import Callable, Dict

import psutil

from .wmi_util import wmi_connection

try:  # py-cpuinfo is optional-but-expected
    import cpuinfo  # type: ignore
except Exception:  # noqa: BLE001
    cpuinfo = None  # type: ignore

try:  # GPUtil only yields data for NVIDIA cards
    import GPUtil  # type: ignore
except Exception:  # noqa: BLE001
    GPUtil = None  # type: ignore

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _cpu_name(log: LogFn) -> str:
    if cpuinfo is not None:
        try:
            info = cpuinfo.get_cpu_info()
            brand = info.get("brand_raw") or info.get("brand")
            if brand:
                return str(brand).strip()
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] py-cpuinfo failed: {exc}")
    # Fallback via WMI
    with wmi_connection() as c:
        if c is not None:
            try:
                for proc in c.Win32_Processor():
                    if proc.Name:
                        return str(proc.Name).strip()
            except Exception as exc:  # noqa: BLE001
                log(f"[WARN] Win32_Processor name failed: {exc}")
    return "Unknown CPU"


def _ram(log: LogFn) -> Dict[str, object]:
    total_bytes = psutil.virtual_memory().total
    total_gb = round(total_bytes / (1024 ** 3), 1)
    speed_mhz = None
    module_count = 0
    mem_type = None
    with wmi_connection() as c:
        if c is not None:
            try:
                for stick in c.Win32_PhysicalMemory():
                    module_count += 1
                    if stick.Speed:
                        speed_mhz = int(stick.Speed)
                    if getattr(stick, "SMBIOSMemoryType", None):
                        mem_type = _smbios_mem_type(int(stick.SMBIOSMemoryType))
            except Exception as exc:  # noqa: BLE001
                log(f"[WARN] Win32_PhysicalMemory failed: {exc}")
    return {
        "total_gb": total_gb,
        "speed_mhz": speed_mhz,
        "modules": module_count,
        "type": mem_type,
    }


def _smbios_mem_type(code: int) -> str:
    # Common SMBIOS memory-type codes; fall back to the raw code.
    table = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5"}
    return table.get(code, f"type-{code}")


def _gpus(log: LogFn) -> list[Dict[str, object]]:
    results: list[Dict[str, object]] = []

    # Primary: WMI enumerates every adapter (Intel/AMD/NVIDIA).
    with wmi_connection() as c:
        if c is not None:
            try:
                for ctrl in c.Win32_VideoController():
                    name = (ctrl.Name or "Unknown GPU").strip()
                    # AdapterRAM is a signed 32-bit field: unreliable >4GB,
                    # so we treat it as a hint only.
                    vram_mb = None
                    if getattr(ctrl, "AdapterRAM", None):
                        try:
                            vram_mb = int(ctrl.AdapterRAM) / (1024 ** 2)
                            if vram_mb < 0:  # overflow artefact
                                vram_mb = None
                        except Exception:  # noqa: BLE001
                            vram_mb = None
                    results.append({"name": name, "vram_mb": vram_mb})
            except Exception as exc:  # noqa: BLE001
                log(f"[WARN] Win32_VideoController failed: {exc}")

    # Enrich NVIDIA entries with accurate VRAM from GPUtil where possible.
    if GPUtil is not None:
        try:
            for g in GPUtil.getGPUs():
                matched = next(
                    (r for r in results if g.name.lower() in str(r["name"]).lower()),
                    None,
                )
                if matched:
                    matched["vram_mb"] = float(g.memoryTotal)
                else:
                    results.append({"name": g.name, "vram_mb": float(g.memoryTotal)})
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] GPUtil enumeration failed: {exc}")

    if not results:
        results.append({"name": "Unknown GPU", "vram_mb": None})
    return results


def _board_and_bios(log: LogFn) -> Dict[str, object]:
    out: Dict[str, object] = {
        "board_mfr": None,
        "board_model": None,
        "bios_vendor": None,
        "bios_version": None,
        "bios_date": None,
    }
    with wmi_connection() as c:
        if c is None:
            return out
        try:
            for b in c.Win32_BaseBoard():
                out["board_mfr"] = (b.Manufacturer or "").strip() or None
                out["board_model"] = (b.Product or "").strip() or None
                break
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] Win32_BaseBoard failed: {exc}")
        try:
            for bios in c.Win32_BIOS():
                out["bios_vendor"] = (bios.Manufacturer or "").strip() or None
                ver = bios.SMBIOSBIOSVersion or (bios.Version or "")
                out["bios_version"] = str(ver).strip() or None
                # ReleaseDate comes back as YYYYMMDD... (CIM_DATETIME)
                if bios.ReleaseDate:
                    raw = str(bios.ReleaseDate)
                    out["bios_date"] = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
                break
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] Win32_BIOS failed: {exc}")
    return out


def collect_core_specs(log: LogFn | None = None) -> Dict[str, object]:
    """Gather core hardware specs. ``log`` receives console lines."""
    log = log or _noop

    log("[SPEC] Enumerating processor ...")
    cpu_name = _cpu_name(log)
    phys = psutil.cpu_count(logical=False) or 0
    logical = psutil.cpu_count(logical=True) or 0
    try:
        base_mhz = round((psutil.cpu_freq().max or 0))
    except Exception:  # noqa: BLE001
        base_mhz = 0
    log(f"[SPEC]   -> {cpu_name} ({phys}C/{logical}T @ {base_mhz} MHz)")

    log("[SPEC] Measuring installed memory ...")
    ram = _ram(log)
    log(f"[SPEC]   -> {ram['total_gb']} GB "
        f"{ram.get('type') or ''} {ram.get('speed_mhz') or '?'} MHz "
        f"across {ram['modules'] or '?'} module(s)")

    log("[SPEC] Scanning display adapters ...")
    gpus = _gpus(log)
    for g in gpus:
        vram = f"{g['vram_mb']:.0f} MB" if g["vram_mb"] else "VRAM unknown"
        log(f"[SPEC]   -> {g['name']} ({vram})")

    log("[SPEC] Reading motherboard / BIOS identity ...")
    board = _board_and_bios(log)
    log(f"[SPEC]   -> {board.get('board_mfr') or '?'} "
        f"{board.get('board_model') or '?'} | BIOS "
        f"{board.get('bios_version') or '?'} ({board.get('bios_date') or '?'})")

    return {
        "cpu": {
            "name": cpu_name,
            "cores_physical": phys,
            "cores_logical": logical,
            "base_mhz": base_mhz,
        },
        "ram": ram,
        "gpus": gpus,
        "board": board,
    }
