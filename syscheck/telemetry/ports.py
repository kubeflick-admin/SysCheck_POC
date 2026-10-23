"""Port / peripheral enumeration for the ports test.

An OS cannot tell whether a *physical* port is sound while nothing is
plugged into it, so the ports test is driven by live arrival/removal
events: the operator plugs a device into each port in turn and we diff the
USB device set between polls. This module provides the Qt-free building
blocks:

    list_usb_devices()    -> {device_id: friendly_name} of attached USB PnP
    list_display_outputs() -> connected external display connectors (HDMI …)

Both degrade to empty results when WMI is unavailable (non-Windows dev box).
"""

from __future__ import annotations

from typing import Callable, Dict, List

from .wmi_util import wmi_connection

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def list_usb_devices() -> Dict[str, str]:
    """Return ``{DeviceID: friendly name}`` for every attached USB device.

    Matching on a ``USB`` device-id prefix captures hubs, storage, input
    devices and composite peripherals. The filter is pushed into WQL so the
    provider only materialises USB rows — enumerating the *whole*
    ``Win32_PnPEntity`` class is seconds slower and far too costly to poll.
    """
    devices: Dict[str, str] = {}
    with wmi_connection("root\\cimv2") as c:
        if c is None:
            return devices
        try:
            rows = c.query("SELECT DeviceID, Name FROM Win32_PnPEntity "
                           "WHERE DeviceID LIKE 'USB%'")
            for e in rows:
                dev_id = getattr(e, "DeviceID", None) or ""
                if not dev_id:
                    continue
                devices[dev_id] = getattr(e, "Name", None) or dev_id
        except Exception:  # noqa: BLE001
            pass
    return devices


# VideoOutputTechnology codes from WmiMonitorConnectionParams (root\wmi).
_CONNECTOR = {
    0: "VGA", 1: "S-Video", 2: "Composite", 3: "Component", 4: "DVI",
    5: "HDMI", 6: "LVDS (internal)", 8: "D-Jpn", 9: "SDI",
    10: "DisplayPort (ext)", 11: "DisplayPort (embedded)",
    12: "UDI (ext)", 13: "UDI (embedded)", 14: "SDTV", 15: "Miracast",
    # The internal panel reports 0x80000000, which WMI hands back as a
    # signed 32-bit value (-2147483648).
    -2: "Internal", 0x80000000: "Internal", -2147483648: "Internal",
}


def list_display_outputs(log: LogFn | None = None) -> List[str]:
    """Best-effort list of connected display connectors (e.g. ``HDMI``).

    Uses ``WmiMonitorConnectionParams`` where available; otherwise falls
    back to the plain video-controller names so the card is never empty.
    """
    log = log or _noop
    outputs: List[str] = []
    with wmi_connection("root\\wmi") as c:
        if c is not None:
            try:
                for m in c.WmiMonitorConnectionParams():
                    code = int(getattr(m, "VideoOutputTechnology", -1))
                    outputs.append(_CONNECTOR.get(code, f"Output #{code}"))
            except Exception as exc:  # noqa: BLE001
                log(f"[PORT] monitor connection params n/a: {exc}")
    if outputs:
        return outputs

    with wmi_connection("root\\cimv2") as c:
        if c is not None:
            try:
                for v in c.Win32_VideoController():
                    name = getattr(v, "Name", None)
                    if name:
                        outputs.append(name)
            except Exception:  # noqa: BLE001
                pass
    return outputs
