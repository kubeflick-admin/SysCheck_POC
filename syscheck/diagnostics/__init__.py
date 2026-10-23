"""User-guided diagnostics subsystem (Module 2).

    camera   -> OpenCV Laplacian-variance sharpness scoring
    keyboard -> keyboard layout data + actuation state machine
    touchpad -> cursor-coverage grid + click/scroll gesture tracking

All modules are deliberately free of any Qt import so the algorithms can
be unit-tested headlessly; the Qt views in ``syscheck.ui.views`` drive
them.
"""

from .camera import laplacian_variance, classify_sharpness
from .keyboard import KEYBOARD_ROWS, KeyboardState
from .touchpad import TouchpadState, GESTURES

__all__ = [
    "laplacian_variance",
    "classify_sharpness",
    "KEYBOARD_ROWS",
    "KeyboardState",
    "TouchpadState",
    "GESTURES",
]
