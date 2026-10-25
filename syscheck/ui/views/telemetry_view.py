"""Telemetry view — drives :class:`TelemetryWorker` and fills in spec,
storage, and battery cards as each stage reports back."""

from __future__ import annotations

from html import escape
from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                               QPushButton, QWidget)

from ... import config
from ...diskhealth.describe import (SOURCE_LABELS, assess, condition,
                                    data_words, on_time_words, size_words,
                                    temperature_words)
from ...scoring import score_battery, score_storage
from ...workers import TelemetryWorker
from ..widgets import CircularProgress
from ._common import health_color, label, light_state, panel


def _card(title: str) -> tuple:
    frame = panel()
    lay = QVBoxLayout(frame)
    lay.addWidget(label(title, "h2"))
    body = label("· awaiting scan …", "mono")
    body.setWordWrap(True)
    body.setTextFormat(Qt.RichText)
    body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    lay.addWidget(body, 1)
    return frame, body


class TelemetryView(QWidget):
    log = Signal(str)
    status = Signal(str, str)     # component key, state
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: Dict[str, object] = {}
        self._worker: TelemetryWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        self._ring = CircularProgress(diameter=150)
        self._ring.set_color(config.COL_BLUE)
        self._ring.set_caption("0%", "SWEEPING")
        top.addWidget(self._ring, 0, Qt.AlignTop)

        intro = QVBoxLayout()
        intro.addWidget(label("AUTOMATED HARDWARE TELEMETRY", "h2"))
        intro.addWidget(label("Silently profiling processor, memory, graphics, "
                              "storage S.M.A.R.T. and battery wear. No input "
                              "required.", "dim"))
        intro.addStretch(1)
        top.addLayout(intro, 1)
        root.addLayout(top)

        grid = QGridLayout()
        grid.setSpacing(12)
        self._sys_card, self._sys_body = _card("SYSTEM")
        self._stor_card, self._stor_body = _card("STORAGE")
        self._batt_card, self._batt_body = _card("BATTERY")
        grid.addWidget(self._sys_card, 0, 0, 2, 1)
        grid.addWidget(self._stor_card, 0, 1)
        grid.addWidget(self._batt_card, 1, 1)
        root.addLayout(grid, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn = QPushButton("PROCEED TO STORAGE  ▶")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self.next_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        self._btn.setEnabled(False)
        self._ring.set_value(0)
        self._ring.set_caption("0%", "SWEEPING")
        for b in (self._sys_body, self._stor_body, self._batt_body):
            b.setText("· scanning …")
        for key in ("storage", "battery", "thermal", "camera", "keyboard"):
            self.status.emit(key, "active" if key in ("storage", "battery")
                             else "idle")

        self._worker = TelemetryWorker()
        self._worker.log.connect(self.log.emit)
        self._worker.progress.connect(self._on_progress)
        self._worker.component_done.connect(self._on_component)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def stop_worker(self) -> None:
        # A WMI query in flight can't be interrupted; stop after the stage.
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(10000)

    # ── slots ─────────────────────────────────────────────────────────
    def _on_progress(self, pct: int) -> None:
        self._ring.set_value(pct)
        self._ring.set_caption(f"{pct}%", "SWEEPING")

    def _on_component(self, key: str, data: dict) -> None:
        if key == "specs":
            self._sys_body.setText(self._fmt_specs(data))
        elif key == "storage":
            self._stor_body.setText(self._fmt_storage(data))
            self.status.emit("storage",
                             light_state(score_storage(data).status))
        elif key == "battery":
            self._batt_body.setText(self._fmt_battery(data))
            self.status.emit("battery",
                             light_state(score_battery(data).status))

    def _on_finished(self, results: dict) -> None:
        self._results = results
        self._ring.set_value(100)
        self._ring.set_caption("DONE", "TELEMETRY")
        self._ring.set_color(config.COL_GREEN)
        self._btn.setEnabled(True)
        self.finished.emit(results)

    # ── formatting ────────────────────────────────────────────────────
    @staticmethod
    def _row(k: str, v: str) -> str:
        return (f"<span style='color:{config.COL_TEXT_DIM}'>{k:<10}</span>"
                f"<span style='color:{config.COL_TEXT}'>{v}</span><br>")

    def _fmt_specs(self, d: dict) -> str:
        cpu = d.get("cpu", {})
        ram = d.get("ram", {})
        gpus = d.get("gpus", [])
        board = d.get("board", {})
        out = self._row("CPU", f"{cpu.get('name', '?')}")
        out += self._row("", f"{cpu.get('cores_physical', '?')}C / "
                             f"{cpu.get('cores_logical', '?')}T @ "
                             f"{cpu.get('base_mhz', '?')} MHz")
        out += self._row("RAM", f"{ram.get('total_gb', '?')} GB "
                               f"{ram.get('type') or ''} "
                               f"{ram.get('speed_mhz') or '?'} MHz")
        for g in gpus:
            vram = f"{g['vram_mb']:.0f} MB" if g.get("vram_mb") else "VRAM ?"
            out += self._row("GPU", f"{g['name']} ({vram})")
        out += self._row("BOARD", f"{board.get('board_mfr') or '?'} "
                                 f"{board.get('board_model') or ''}")
        out += self._row("BIOS", f"{board.get('bios_version') or '?'} "
                                f"({board.get('bios_date') or '?'})")
        return out

    def _fmt_storage(self, d: dict) -> str:
        drives = d.get("drives", [])
        if not drives:
            return "· no drive telemetry"
        out = ""
        for dr in drives:
            a = assess(dr)
            pct = a.health.health_pct
            health = (f"{pct}% {condition(pct)}" if pct is not None
                      else "unknown")
            out += self._row("DRIVE", escape(a.name))
            out += self._row("", f"{dr.get('kind')} · "
                                 f"{size_words(dr.get('size_gb'))}")
            out += self._row("HEALTH", f"<span style='color:"
                                       f"{health_color(pct)}'>{health}</span>")
            out += self._row("TEMP", temperature_words(
                dr.get("temperature_c"), dr.get("temperature_max_c")))
            out += self._row("ON-TIME", on_time_words(dr.get("power_on_hours")))
            out += self._row("WRITTEN", data_words(dr.get("host_writes_tb")))
            out += self._row("SOURCE", SOURCE_LABELS.get(dr.get("source"),
                                                         "?"))
        if not d.get("smart_readable", False):
            out += (f"<span style='color:{config.COL_AMBER}'>"
                    f"· run elevated for full S.M.A.R.T.</span>")
        return out

    def _fmt_battery(self, d: dict) -> str:
        if not d.get("present"):
            return "· no battery (desktop)"
        cycles = d.get("cycles")
        cycles_str = str(cycles) if cycles is not None else "n/a"
        wear = d.get("wear_pct")
        return (self._row("WEAR", f"{wear}%" if wear is not None else "n/a")
                + self._row("DESIGN", f"{d.get('design_mwh') or '?'} mWh")
                + self._row("FULL", f"{d.get('full_mwh') or '?'} mWh")
                + self._row("CYCLES", cycles_str))
