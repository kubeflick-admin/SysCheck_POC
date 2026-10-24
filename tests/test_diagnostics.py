"""Qt-free diagnostic models: SMART parsing, keyboard and touchpad state."""

from syscheck.diagnostics.keyboard import KeyboardState, all_labels
from syscheck.diagnostics.touchpad import GESTURES, TouchpadState
from syscheck.telemetry.storage import (ATTR_POWER_ON_HOURS,
                                        ATTR_TOTAL_LBA_WRITTEN,
                                        _parse_smart_blob)


# ── S.M.A.R.T. ─────────────────────────────────────────────────────────
def _record(attr_id: int, raw: int) -> bytes:
    # id, flags(2), current, worst, raw(6 LE), reserved
    return bytes([attr_id, 0, 0, 100, 100]) + raw.to_bytes(6, "little") + b"\0"


def test_parse_smart_blob_reads_raw_values():
    blob = (b"\x10\x00"
            + _record(ATTR_POWER_ON_HOURS, 12_345)
            + _record(0, 999)                       # empty slot: ignored
            + _record(ATTR_TOTAL_LBA_WRITTEN, 2**40))
    blob += b"\0" * (512 - len(blob))
    assert _parse_smart_blob(blob) == {ATTR_POWER_ON_HOURS: 12_345,
                                       ATTR_TOTAL_LBA_WRITTEN: 2**40}


def test_parse_smart_blob_tolerates_truncated_record():
    # Only the first raw byte survives the cut; parsing must not raise.
    assert _parse_smart_blob(b"\x10\x00" + _record(9, 7)[:6]) == {9: 7}


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
