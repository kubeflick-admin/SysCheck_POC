"""Qt-free diagnostic models: keyboard and touchpad state.

S.M.A.R.T. blob parsing is covered in ``test_wmi_smart.py``.
"""

from syscheck.diagnostics.keyboard import KeyboardState, all_labels
from syscheck.diagnostics.touchpad import GESTURES, TouchpadState


# ── keyboard ───────────────────────────────────────────────────────────
def test_layout_labels_are_unique_and_reachable():
    labels = all_labels()
    assert len(labels) == len(set(labels))
    assert "Fn" not in labels                    # never reaches the OS
    assert {"←", "↑", "↓", "→", "RCtrl", "RAlt"} <= set(labels)


def test_keyboard_state_tracks_coverage():
    state = KeyboardState()
    assert state.press("A") is True
    assert state.press("A") is False             # repeat doesn't count twice
    assert state.press("NotAKey") is False
    assert state.pressed_count() == 1
    for lbl in all_labels():
        state.press(lbl)
    assert state.all_done()
    assert state.summary()["coverage_pct"] == 100.0


# ── touchpad ───────────────────────────────────────────────────────────
def test_touchpad_coverage_and_gestures():
    state = TouchpadState(cols=2, rows=2)
    assert state.visit(0.1, 0.1) == (0, 0)
    assert state.visit(0.2, 0.2) is None          # same cell
    assert state.visit(1.0, 1.0) == (1, 1)        # edge clamps into the grid
    assert state.coverage() == 0.5
    state.visit(0.9, 0.1)
    state.visit(0.1, 0.9)
    for gesture, _label in GESTURES:
        state.mark(gesture)
    assert state.all_done()
    assert state.summary()["missing_gestures"] == []
