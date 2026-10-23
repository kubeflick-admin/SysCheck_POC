"""Small helpers for talking to WMI from inside Qt worker threads.

WMI rides on COM, and COM must be initialised **per thread**. The Qt
workers run telemetry off the GUI thread, so every collector that touches
WMI opens its connection through :func:`wmi_connection`, which handles
``CoInitialize`` / ``CoUninitialize`` and degrades gracefully when the
``wmi`` / ``pywin32`` stack is unavailable (e.g. on a non-Windows dev box).
"""

from __future__ import annotations

import contextlib
from typing import Iterator, Optional

try:  # pragma: no cover - import guard for non-Windows environments
    import pythoncom  # type: ignore
    import wmi  # type: ignore

    _WMI_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure means "not available"
    pythoncom = None  # type: ignore
    wmi = None  # type: ignore
    _WMI_AVAILABLE = False


def wmi_available() -> bool:
    """True when the WMI/pywin32 stack imported successfully."""
    return _WMI_AVAILABLE


@contextlib.contextmanager
def wmi_connection(namespace: str = "root\\cimv2") -> Iterator[Optional["wmi.WMI"]]:
    """Yield a thread-safe WMI connection, or ``None`` if WMI is unavailable.

    Usage::

        with wmi_connection() as c:
            if c is None:
                ...  # handle "no WMI" path
            else:
                for board in c.Win32_BaseBoard():
                    ...
    """
    if not _WMI_AVAILABLE:
        yield None
        return

    pythoncom.CoInitialize()
    try:
        yield wmi.WMI(namespace=namespace)
    finally:
        pythoncom.CoUninitialize()
