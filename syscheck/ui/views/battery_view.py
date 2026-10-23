"""Battery discharge view.

Static wear (design vs full-charge capacity) is gathered silently during
the telemetry sweep; this step adds the *dynamic* check the operator asked
for: with the charger pulled, how long does the pack take to shed one
percent, and under how much CPU load? It drives :class:`BatteryDischargeWorker`,
nagging to unplug AC first, then clocks the 1% drop and derives the draw in
watts.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QWidget

from ... import config
from ...workers import BatteryDischargeWorker
from ..widgets import CircularProgress
from ._common import label, panel


class BatteryView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[BatteryDischargeWorker] = None
        self._result: Dict[str, object] = {}
        self._full_mwh: Optional[int] = None
        self._skipped = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        self._ring = CircularProgress(diameter=150)
        self._ring.set_color(config.COL_AMBER)
        self._ring.set_caption("—", "BATTERY")
        top.addWidget(self._ring, 0, Qt.AlignTop)

        intro = QVBoxLayout()
        intro.addWidget(label("BATTERY DISCHARGE TEST", "h2"))
        intro.addWidget(label(
            "Wear is already measured from capacity. This step times a live "
            f"{config.BATTERY_DISCHARGE_DROP_PCT}% discharge: unplug the "
            "charger and we clock how long 1% takes and the load driving it.",
            "dim"))
        self._prompt = label("", "mono")
        self._prompt.setWordWrap(True)
        intro.addWidget(self._prompt)
        intro.addStretch(1)
        top.addLayout(intro, 1)
        root.addLayout(top)

        # Live readout card
        card = panel(accent=True)
        cl = QVBoxLayout(card)
        cl.addWidget(label("LIVE DISCHARGE READOUT", "dim"))
        self._readout = label("· awaiting battery power …", "mono")
        self._readout.setWordWrap(True)
        self._readout.setTextFormat(Qt.RichText)
        self._readout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        cl.addWidget(self._readout, 1)
        root.addWidget(card, 1)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._btn = QPushButton("PROCEED TO THERMAL  ▶")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self.next_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

    # ── context injection ──────────────────────────────────────────────
    def set_full_mwh(self, full_mwh: Optional[int]) -> None:
        """Supplied by the telemetry sweep so draw can be derived in watts."""
        self._full_mwh = full_mwh

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        self._result = {}
        self._skipped = False
        self._btn.setEnabled(False)
        self._skip_btn.setEnabled(True)
        self._ring.set_color(config.COL_AMBER)
        self._ring.set_value(0)
        self._ring.set_caption("—", "BATTERY")
        self._prompt.setText("Unplug the power cord to begin the discharge test.")
        self._readout.setText("· waiting for the charger to be removed …")
        self.status.emit("battery", "active")

        self._worker = BatteryDischargeWorker(self._full_mwh)
        self._worker.log.connect(self.log.emit)
        self._worker.sample.connect(self._on_sample)
        self._worker.finished_discharge.connect(self._on_finished)
        self._worker.start()

    def stop_worker(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)

    # ── slots ────────────────────────────────────────────────────────────
    def _on_sample(self, phase: str, percent, cpu: float, elapsed: float) -> None:
        pct_txt = f"{percent}%" if percent is not None else "—"
        if phase == "wait":
            self._ring.set_color(config.COL_AMBER)
            self._ring.set_caption("UNPLUG", "AC IN")
            self._prompt.setText("⚠  Charger still connected — remove the power "
                                 "cord to start the discharge test.")
            self._readout.setText(
                self._row("CHARGE", pct_txt)
                + self._row("STATE", "ON AC — waiting to unplug")
                + self._row("CPU", f"{cpu:.0f}%"))
        else:  # drain
            frac = min(100.0, elapsed / config.BATTERY_DISCHARGE_TIMEOUT_S * 100)
            self._ring.set_color(config.COL_BLUE)
            self._ring.set_value(frac)
            self._ring.set_caption(pct_txt, "ON BATT")
            self._prompt.setText("Running on battery — timing the 1% drop. "
                                 "Keep the charger unplugged.")
            self._readout.setText(
                self._row("CHARGE", pct_txt)
                + self._row("ELAPSED", f"{elapsed:.0f}s")
                + self._row("CPU LOAD", f"{cpu:.0f}%")
                + self._row("STATE", "DISCHARGING"))

    def _on_finished(self, result: dict) -> None:
        if self._skipped:
            return   # operator already left this step; ignore the late signal
        self._result = result
        self._btn.setEnabled(True)
        self._ring.set_value(100)

        state = self._state_for(result)
        self._ring.set_color({"pass": config.COL_GREEN, "warn": config.COL_AMBER,
                              "fail": config.COL_RED,
                              "idle": config.COL_TEXT_DIM}[state])
        self._ring.set_caption("DONE", "DISCHARGE")
        self._prompt.setText("Discharge test complete — you can reconnect the "
                             "charger now.")
        self._readout.setText(self._summary_html(result))
        # The discharge result augments (not replaces) the wear status light.
        if state != "idle":
            self.status.emit("battery", state)
        self.finished.emit(result)

    # ── formatting ────────────────────────────────────────────────────────
    @staticmethod
    def _row(k: str, v: str) -> str:
        return (f"<span style='color:{config.COL_TEXT_DIM}'>{k:<10}</span>"
                f"<span style='color:{config.COL_TEXT}'>{v}</span><br>")

    def _summary_html(self, r: dict) -> str:
        if not r.get("ran"):
            return (f"<span style='color:{config.COL_AMBER}'>· "
                    f"{r.get('reason') or 'Test did not run.'}</span>")
        spp = r.get("seconds_per_pct")
        if spp is None:
            return (f"<span style='color:{config.COL_AMBER}'>· "
                    f"{r.get('reason') or 'Could not determine a discharge rate.'}"
                    f"</span>")
        kind = "measured" if r.get("measured") else "system estimate"
        out = self._row("RATE", f"{spp:.0f}s per 1%  ({kind})")
        if r.get("avg_cpu_pct") is not None:
            out += self._row("CPU LOAD", f"{r['avg_cpu_pct']:.0f}% avg")
        if r.get("drain_w") is not None:
            out += self._row("DRAW", f"≈ {r['drain_w']:.1f} W")
        if r.get("start_pct") is not None and r.get("end_pct") is not None:
            out += self._row("CHARGE", f"{r['start_pct']}% → {r['end_pct']}%")
        return out

    @staticmethod
    def _state_for(r: dict) -> str:
        if not r.get("ran") or r.get("seconds_per_pct") is None:
            return "idle"   # inconclusive — leave the wear verdict standing
        drain = r.get("drain_w")
        if drain is None:
            return "pass"
        if drain >= config.BATTERY_DRAIN_FAIL_W:
            return "fail"
        if drain >= config.BATTERY_DRAIN_WARN_W:
            return "warn"
        return "pass"

    # ── skip ────────────────────────────────────────────────────────────
    def _skip(self) -> None:
        self._skipped = True
        self.stop_worker()
        self._result = {"ran": False, "skipped": True,
                        "reason": "Discharge test skipped."}
        self.finished.emit(self._result)
        self.next_requested.emit()
