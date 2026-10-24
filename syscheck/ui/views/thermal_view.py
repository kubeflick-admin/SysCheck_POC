"""Thermal stress view — live line graph + countdown ring while
:class:`ThermalWorker` drives every core and samples temperature."""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QWidget

from ... import config
from ...scoring import score_thermal
from ...workers import ThermalWorker
from ..widgets import CircularProgress, LineGraph
from ._common import label, light_state, panel


class ThermalView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[ThermalWorker] = None
        self._result: Dict[str, object] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        self._ring = CircularProgress(diameter=150)
        self._ring.set_caption("0s", "LOAD")
        top.addWidget(self._ring, 0, Qt.AlignTop)
        intro = QVBoxLayout()
        intro.addWidget(label("THERMAL STRESS TEST", "h2"))
        intro.addWidget(label(f"Saturating every core for "
                              f"{config.THERMAL_DURATION_S}s while watching the "
                              f"temperature sensor. Flags throttling or any "
                              f"reading over {config.THERMAL_CRITICAL_C:.0f} °C.",
                              "dim"))
        self._peak = label("peak: — °C", "mono")
        intro.addWidget(self._peak)
        intro.addStretch(1)
        top.addLayout(intro, 1)
        root.addLayout(top)

        graph_panel = panel(accent=True)
        gl = QVBoxLayout(graph_panel)
        gl.addWidget(label("CORE TEMPERATURE °C  /  LOAD %", "dim"))
        self._graph = LineGraph(max_points=config.THERMAL_DURATION_S
                                * config.THERMAL_SAMPLE_HZ + 4,
                                y_max=100.0,
                                threshold=config.THERMAL_CRITICAL_C)
        gl.addWidget(self._graph, 1)
        root.addWidget(graph_panel, 1)

        btn_row = QHBoxLayout()
        self._cancel_btn = QPushButton("SKIP TEST")
        self._cancel_btn.setObjectName("ghost")
        self._cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch(1)
        self._btn = QPushButton("PROCEED TO WEBCAM  ▶")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self.next_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

    def start(self) -> None:
        self._btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._graph.clear()
        self._ring.set_color(config.COL_AMBER)
        self._ring.set_caption("0s", "LOAD")
        self.status.emit("thermal", "active")

        self._worker = ThermalWorker()
        self._worker.log.connect(self.log.emit)
        self._worker.sample.connect(self._on_sample)
        self._worker.finished_thermal.connect(self._on_finished)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)

    def stop_worker(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def _on_sample(self, elapsed: float, temp, freq, cpu: float) -> None:
        value = temp if temp is not None else cpu
        self._graph.push(value)
        pct = min(100.0, elapsed / config.THERMAL_DURATION_S * 100)
        self._ring.set_value(pct)
        self._ring.set_caption(f"{elapsed:.0f}s",
                               f"{temp:.0f}°C" if temp is not None
                               else f"{cpu:.0f}% LOAD")
        if temp is not None:
            self._peak.setText(f"current: {temp:.0f} °C")

    def _on_finished(self, result: dict) -> None:
        self._result = result
        self._cancel_btn.setEnabled(False)
        self._btn.setEnabled(True)
        self._ring.set_value(100)

        comp = score_thermal(result)
        state = light_state(comp.status)
        self._ring.set_caption("SKIPPED" if comp.status == "skip" else "DONE",
                               "THERMAL")
        peak = result.get("peak_temp_c")
        self._ring.set_color({"pass": config.COL_GREEN, "warn": config.COL_AMBER,
                              "fail": config.COL_RED,
                              "idle": config.COL_TEXT_DIM}[state])
        self._peak.setText(f"peak: {peak:.0f} °C" if peak is not None
                           else "peak: sensor unavailable")
        self.status.emit("thermal", state)
        self.finished.emit(result)
