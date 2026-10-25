"""Performance-check results for the tests, built around the reference
laptop (exactly config.PERF_REFERENCE, no stutters, nothing wrong)."""

import copy

from syscheck import config

_REF = config.PERF_REFERENCE

_REFERENCE = {
    "completed": True,
    "cpu": {"single": _REF["cpu_single"], "multi": _REF["cpu_multi"],
            "threads": 16, "single_mhz": 4800, "multi_mhz": 3900,
            "sustained_mhz": 3900, "base_mhz": 2200, "throttled": False},
    "memory": {"bandwidth_gbps": _REF["memory"], "installed_gb": 32.0,
               "modules": 2, "slots": 2, "speed_mhz": 5600, "type": "DDR5",
               "channels": "dual"},
    "disk": {"ran": True, "reason": None, "unbuffered": True,
             "folder": "C:\\Users\\me\\AppData\\Local\\Temp", "size_mb": 256,
             "seq_read_mbps": _REF["seq_read"],
             "seq_write_mbps": _REF["seq_write"],
             "rand_read_iops": _REF["rand_read"], "rand_read_ms": 0.1},
    "stutter": {
        "idle": {"ticks": 2990, "seconds": 30.0, "stutters": 0,
                 "freezes": 0, "worst_ms": 12.0},
        "load": {"ticks": 1990, "seconds": 20.0, "stutters": 0,
                 "freezes": 0, "worst_ms": 15.0},
    },
    "idle": {"cpu_pct": 3.0, "ram_used_pct": 35.0, "ram_used_gb": 11.0,
             "ram_total_gb": 31.6, "commit_used_gb": 14.0,
             "commit_limit_gb": 36.0, "disk_busy_pct": 1.0,
             "top_cpu": ["MsMpEng.exe"], "top_ram": ["chrome.exe", "Teams.exe"]},
    "background": {"startup_count": 8, "startup_names": ["OneDrive"],
                   "services_running": 180,
                   "power": {"plan": "Balanced", "mode": "Balanced",
                             "battery_saver": False, "saver": False},
                   "system_drive": "C:", "free_pct": 60.0, "free_gb": 600.0},
    "history": {"available": True, "days": 30, "reason": None,
                "counts": {"hang": 0, "crash": 0, "unexpected_shutdown": 0,
                           "bsod": 0, "disk_error": 0},
                "top_apps": {"hang": [], "crash": []},
                "install_date": "12 Mar 2025"},
}


def reference_perf(**sections):
    """The reference laptop's result, with ``section={field: value}``
    overrides merged in (``section=None`` removes it)."""
    perf = copy.deepcopy(_REFERENCE)
    for name, fields in sections.items():
        if fields is None:
            perf.pop(name, None)
        elif isinstance(fields, dict) and isinstance(perf.get(name), dict):
            perf[name].update(fields)
        else:
            perf[name] = fields
    return perf
