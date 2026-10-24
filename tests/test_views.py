"""View behaviour under an offscreen Qt platform (pytest-qt)."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from syscheck.diagnostics.keyboard import all_labels
from syscheck.telemetry.smart_sources import smartctl
from syscheck.ui.main_window import MainWindow
from syscheck.ui.views import (BatteryView, KeyboardView, ResultsView,
                               TelemetryView, ThermalView)

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"


def _storage(*names):
    drives = [smartctl.parse_device(json.loads(
        (FIXTURES / f"{n}.json").read_text("utf-8"))) for n in names]
    return {"drives": drives, "smart_readable": True}

# Layout label -> (Qt key, native scan code) for keys that have no text.
_SPECIAL_KEYS = {
    "Esc": (Qt.Key_Escape, 0), "Del": (Qt.Key_Delete, 0),
    "Back": (Qt.Key_Backspace, 0), "Tab": (Qt.Key_Tab, 0),
    "Caps": (Qt.Key_CapsLock, 0), "Enter": (Qt.Key_Return, 0),
    "Space": (Qt.Key_Space, 0), "Win": (Qt.Key_Meta, 0),
    "Shift": (Qt.Key_Shift, 0x2A), "RShift": (Qt.Key_Shift, 0x36),
    "Ctrl": (Qt.Key_Control, 0x1D), "RCtrl": (Qt.Key_Control, 0x11D),
    "Alt": (Qt.Key_Alt, 0x38), "RAlt": (Qt.Key_Alt, 0x138),
    "←": (Qt.Key_Left, 0), "↑": (Qt.Key_Up, 0),
    "↓": (Qt.Key_Down, 0), "→": (Qt.Key_Right, 0),
    **{f"F{i}": (getattr(Qt, f"Key_F{i}"), 0) for i in range(1, 13)},
}


def _press(widget, label):
    if label in _SPECIAL_KEYS:
        key, scan = _SPECIAL_KEYS[label]
        text = ""
    else:                                   # printable: Qt key == Latin-1 code
        key, scan, text = ord(label), 0, label.lower()
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(
            widget, QKeyEvent(kind, key, Qt.NoModifier, scan, 0, 0, text))


@pytest.fixture
def keyboard_view(qtbot):
    view = KeyboardView()
    qtbot.addWidget(view)
    view.show()
    view.start()
    yield view
    view._teardown()      # never leave the app-wide key filter installed


def _record(signal):
    seen = []
    signal.connect(lambda *args: seen.append(args))
    return seen


def test_space_during_keyboard_test_does_not_press_a_button(keyboard_view):
    finished = _record(keyboard_view.finished)
    QTest.keyClick(keyboard_view._finish_btn, Qt.Key_Space)
    assert finished == []
    assert keyboard_view._state.is_pressed("Space")


def test_every_key_reaches_100_percent_and_a_green_light(keyboard_view):
    statuses = _record(keyboard_view.status)
    for lbl in all_labels():
        _press(keyboard_view, lbl)
    assert keyboard_view._state.remaining() == set()

    keyboard_view._finish_btn.click()
    assert statuses[-1] == ("keyboard", "pass")


def test_right_hand_modifiers_are_told_apart(keyboard_view):
    _press(keyboard_view, "RCtrl")
    _press(keyboard_view, "RAlt")
    assert keyboard_view._state.is_pressed("RCtrl")
    assert keyboard_view._state.is_pressed("RAlt")
    assert not keyboard_view._state.is_pressed("Ctrl")
    assert not keyboard_view._state.is_pressed("Alt")


def test_thermal_skip_shows_as_skipped(qtbot):
    view = ThermalView()
    qtbot.addWidget(view)
    statuses = _record(view.status)
    view._on_finished({"completed": False, "temp_available": True,
                       "peak_temp_c": 45.0, "throttled": False})
    assert statuses[-1] == ("thermal", "idle")


def test_battery_light_combines_wear_and_discharge(qtbot):
    view = BatteryView()
    qtbot.addWidget(view)
    statuses = _record(view.status)
    view.set_battery({"present": True, "wear_pct": 45.0, "full_mwh": 30_000})
    # A healthy draw can't hide a badly worn pack.
    view._on_finished({"ran": True, "method": "rate", "drain_w": 8.0,
                       "seconds_per_pct": 135.0})
    assert statuses[-1] == ("battery", "fail")


def test_results_with_nothing_measured_render_na(qtbot):
    view = ResultsView()
    qtbot.addWidget(view)
    view.set_results({})
    assert view._headline.text().startswith("NO GRADE")


def test_closing_the_window_stops_every_worker(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    stopped = []
    for view, method in ((window._thermal, "stop_worker"),
                         (window._telemetry, "stop_worker"),
                         (window._battery, "stop_worker"),
                         (window._ports, "stop_worker"),
                         (window._camera, "stop_camera")):
        monkeypatch.setattr(view, method,
                            lambda v=view: stopped.append(type(v).__name__))
    window.close()
    assert sorted(stopped) == ["BatteryView", "CameraView", "PortsView",
                               "TelemetryView", "ThermalView"]


def test_telemetry_card_shows_drive_health(qtbot):
    view = TelemetryView()
    qtbot.addWidget(view)
    text = view._fmt_storage(_storage("nvme_healthy", "sata_ssd_bad_sectors"))
    assert "98% EXCELLENT" in text
    assert "78% FAIR" in text
    assert "about 6 months switched on" in text


def test_storage_technical_details_start_collapsed(qtbot):
    view = ResultsView()
    qtbot.addWidget(view)
    view.set_results({"storage": _storage("sata_ssd_bad_sectors")})
    toggle = next(b for b in view.findChildren(QPushButton)
                  if "TECHNICAL DETAILS" in b.text())
    body = next(lbl for lbl in view.findChildren(QLabel)
                if "Reallocated sectors (ID 5)" in lbl.text())
    assert body.isHidden()
    toggle.click()
    assert not body.isHidden()
    assert toggle.text().startswith("▾")
