"""Main application window: a persistent telemetry console + subsystem
status rail on the right, and a stacked, step-by-step diagnostic flow on
the left.
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QStackedWidget)

from .. import config
from .widgets import TelemetryConsole, IndicatorLight
from .views import (SplashView, TelemetryView, StorageView, BatteryView,
                    ThermalView, CameraView, KeyboardView, TouchpadView,
                    PortsView, ResultsView)
from .views._common import StepHeader, panel, label

_STEPS = ["Telemetry", "Storage", "Battery", "Thermal", "Webcam", "Keyboard",
          "Touchpad", "Ports", "Verdict"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_TITLE)
        self.setMinimumSize(1180, 760)
        self._results: Dict[str, object] = {}

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(16)

        # ── Left: header + stacked views ─────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(14)
        self._header = StepHeader(_STEPS)
        left.addWidget(self._header)

        self._stack = QStackedWidget()
        left.addWidget(self._stack, 1)
        outer.addLayout(left, 1)

        # ── Right: console + status rail ─────────────────────────────
        outer.addWidget(self._build_right_rail(), 0)

        # ── Views ────────────────────────────────────────────────────
        self._splash = SplashView()
        self._telemetry = TelemetryView()
        self._storage = StorageView()
        self._battery = BatteryView()
        self._thermal = ThermalView()
        self._camera = CameraView()
        self._keyboard = KeyboardView()
        self._touchpad = TouchpadView()
        self._ports = PortsView()
        self._results_view = ResultsView()
        for v in (self._splash, self._telemetry, self._storage, self._battery,
                  self._thermal, self._camera, self._keyboard, self._touchpad,
                  self._ports, self._results_view):
            self._stack.addWidget(v)

        self._wire()
        self._stack.setCurrentWidget(self._splash)
        self._header.setVisible(False)
        self._splash.play_boot()

    # ── right rail ────────────────────────────────────────────────────
    def _build_right_rail(self) -> QWidget:
        rail = QWidget()
        rail.setFixedWidth(380)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        console_panel = panel(accent=True)
        cp = QVBoxLayout(console_panel)
        cp.addWidget(label("ACTIVE TELEMETRY CONSOLE", "h2"))
        cp.addWidget(label("live sensor + thread readout", "dim"))
        self._console = TelemetryConsole(max_lines=600)
        cp.addWidget(self._console, 1)
        lay.addWidget(console_panel, 1)

        status_panel = panel()
        sp = QVBoxLayout(status_panel)
        sp.addWidget(label("SUBSYSTEM STATUS", "h2"))
        self._lights: Dict[str, IndicatorLight] = {}
        for key, text in (("storage", "Storage S.M.A.R.T."),
                          ("battery", "Battery wear / drain"),
                          ("thermal", "Thermal / throttle"),
                          ("camera", "Webcam clarity"),
                          ("keyboard", "Keyboard continuity"),
                          ("touchpad", "Touchpad continuity"),
                          ("ports", "Ports / peripherals")):
            light = IndicatorLight(text)
            self._lights[key] = light
            sp.addWidget(light)
        lay.addWidget(status_panel, 0)
        return rail

    # ── wiring ────────────────────────────────────────────────────────
    def _wire(self) -> None:
        self._splash.start_requested.connect(self._start_flow)

        for view in (self._telemetry, self._battery, self._thermal,
                     self._camera, self._keyboard, self._touchpad,
                     self._ports):
            view.log.connect(self._console.log)
            view.status.connect(self._set_status)

        self._telemetry.finished.connect(
            lambda d: self._results.update({"specs": d.get("specs"),
                                            "storage": d.get("storage"),
                                            "battery": d.get("battery")}))
        self._telemetry.next_requested.connect(self._enter_storage)
        self._storage.next_requested.connect(self._enter_battery)

        # The discharge test augments the wear data gathered in telemetry.
        self._battery.finished.connect(self._merge_battery)
        self._battery.next_requested.connect(
            lambda: self._advance(self._thermal, 3))

        self._thermal.finished.connect(
            lambda d: self._results.__setitem__("thermal", d))
        self._thermal.next_requested.connect(
            lambda: self._advance(self._camera, 4))

        self._camera.finished.connect(
            lambda d: self._results.__setitem__("camera", d))
        self._camera.next_requested.connect(
            lambda: self._advance(self._keyboard, 5))

        self._keyboard.finished.connect(
            lambda d: self._results.__setitem__("keyboard", d))
        self._keyboard.next_requested.connect(
            lambda: self._advance(self._touchpad, 6))

        self._touchpad.finished.connect(
            lambda d: self._results.__setitem__("touchpad", d))
        self._touchpad.next_requested.connect(
            lambda: self._advance(self._ports, 7))

        self._ports.finished.connect(
            lambda d: self._results.__setitem__("ports", d))
        self._ports.next_requested.connect(self._show_results)

        self._results_view.restart_requested.connect(self._reset_flow)
        self._results_view.log.connect(self._console.log)

    def _enter_storage(self) -> None:
        self._storage.set_storage(self._results.get("storage"))
        self._advance(self._storage, 1)

    def _enter_battery(self) -> None:
        # Hand the telemetry battery data to the discharge step: full-charge
        # energy converts a 1%-drop time into watts, and wear feeds its light.
        self._battery.set_battery(self._results.get("battery"))
        self._advance(self._battery, 2)

    def _merge_battery(self, discharge: dict) -> None:
        batt = self._results.get("battery")
        if not isinstance(batt, dict):
            batt = {}
            self._results["battery"] = batt
        batt["discharge"] = discharge

    # ── navigation ────────────────────────────────────────────────────
    def _start_flow(self) -> None:
        self._results = {}
        for light in self._lights.values():
            light.set_state("idle")
        self._header.setVisible(True)
        self._advance(self._telemetry, 0)

    def _advance(self, view: QWidget, step: int) -> None:
        self._console.log(f"==== NAV :: {_STEPS[step].upper()} ====")
        self._header.set_step(step)
        self._stack.setCurrentWidget(view)
        if hasattr(view, "start"):
            view.start()

    def _show_results(self) -> None:
        self._console.log("==== NAV :: VERDICT ====")
        self._header.set_step(len(_STEPS) - 1)
        self._results_view.set_results(self._results)
        self._stack.setCurrentWidget(self._results_view)

    def _reset_flow(self) -> None:
        self._console.clear()
        self._stack.setCurrentWidget(self._splash)
        self._header.setVisible(False)
        self._splash.play_boot()

    def _set_status(self, key: str, state: str) -> None:
        light = self._lights.get(key)
        if light:
            light.set_state(state)

    # ── graceful shutdown ─────────────────────────────────────────────
    def closeEvent(self, event) -> None:  # noqa: N802
        # Make sure no worker thread outlives the window: a QThread destroyed
        # while still running aborts the whole process.
        for stop in (self._camera.stop_camera, self._battery.stop_worker,
                     self._ports.stop_worker, self._thermal.stop_worker,
                     self._telemetry.stop_worker):
            try:
                stop()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)
