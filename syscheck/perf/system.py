"""Windows settings and inventory, parsed and judged (pure).

    power_mode()       ``powercfg /getactivescheme`` plus the Windows 10/11
                       "Power mode" overlay and battery saver
    enabled_startup()  Run keys and Startup folders, less what Task Manager
                       has turned off
    memory_channels()  single or dual channel from the installed modules
    discrete_gpu()     whether a separate graphics card is listed
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

POWER_SCHEMES = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Power saver",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Ultimate Performance",
}
# Settings > System > Power > Power mode, an overlay on the Balanced plan.
POWER_OVERLAYS = {
    "00000000-0000-0000-0000-000000000000": "Balanced",
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "Best power efficiency",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "Better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "Best performance",
}
SAVER_NAMES = ("Power saver", "Best power efficiency")

_SCHEME_RE = re.compile(
    r"([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\s*(?:\(([^()]*)\))?", re.I)
_CHANNEL_RE = re.compile(r"channel\s*-?\s*([a-h])(?![a-z])", re.I)
_DISCRETE_RE = re.compile(
    r"nvidia|geforce|quadro|radeon(?:\(tm\))?\s*(?:rx|pro)\b"
    r"|arc(?:\(tm\))?\s*a\d", re.I)


def parse_active_scheme(text: str) -> Tuple[Optional[str], Optional[str]]:
    """``powercfg /getactivescheme`` -> (GUID, the plan's own name).

    "Power Scheme GUID: 381b4222-...  (Balanced)"; the name is in the
    Windows display language, so known GUIDs are named by us instead.
    """
    match = _SCHEME_RE.search(text or "")
    if not match:
        return None, None
    return match.group(1).lower(), (match.group(2) or "").strip() or None


def power_mode(scheme_text: str, overlay_guid: Optional[str] = None,
               battery_saver: bool = False) -> Dict[str, object]:
    """The power plan and mode, and whether either slows the laptop."""
    guid, own_name = parse_active_scheme(scheme_text)
    plan = POWER_SCHEMES.get(guid or "") or own_name
    mode = None
    if plan == "Balanced" and overlay_guid:
        mode = POWER_OVERLAYS.get(overlay_guid.strip("{}").lower())
    return {"plan": plan, "mode": mode, "battery_saver": bool(battery_saver),
            "saver": plan in SAVER_NAMES or mode in SAVER_NAMES
            or bool(battery_saver)}


def enabled_startup(entries: Iterable[Tuple[str, str]],
                    approved: Mapping[Tuple[str, str], bytes]) -> List[str]:
    """Names of the startup programs that will actually run.

    ``entries`` are (group, name): group is the ``StartupApproved`` key
    that governs the entry ("Run", "Run32" or "StartupFolder") and name
    the Run value or shortcut file name. ``approved`` maps
    (group, lower-case name) to its ``StartupApproved`` value; Task
    Manager marks an item it has turned off with an odd first byte.
    """
    names: List[str] = []
    seen = set()
    for group, name in entries:
        key = (group, (name or "").lower())
        if not name or key in seen:
            continue
        seen.add(key)
        flag = approved.get(key)
        if flag and flag[0] & 1:
            continue
        names.append(name)
    return names


def memory_channels(modules: Iterable[Mapping[str, object]]) -> Optional[str]:
    """"single" or "dual" channel memory, or None without modules.

    The slot labels name the channel on most laptops ("ChannelA-DIMM0",
    "CHANNEL B"); otherwise one module means single channel.
    """
    modules = list(modules)
    if not modules:
        return None
    letters = set()
    for module in modules:
        for text in (module.get("bank"), module.get("locator")):
            match = _CHANNEL_RE.search(str(text or ""))
            if match:
                letters.add(match.group(1).upper())
    if letters:
        return "dual" if len(letters) >= 2 else "single"
    return "single" if len(modules) == 1 else "dual"


def discrete_gpu(gpus: object) -> bool:
    """True when a separate graphics card (NVIDIA, Radeon RX/Pro, Arc A)
    is listed; integrated Intel / AMD graphics are not."""
    if not isinstance(gpus, list):
        return False
    return any(_DISCRETE_RE.search(str((g or {}).get("name") or ""))
               for g in gpus if isinstance(g, dict))
