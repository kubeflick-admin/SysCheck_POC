"""Telemetry view — drives :class:`TelemetryWorker` and fills in spec,
storage, and battery cards as each stage reports back."""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                               QPushButton, QWidget)

from ... import config
from ...workers import TelemetryWorker
from ..widgets import CircularProgress
from ._common import label, panel


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
        self._btn = QPushButton("PROCEED TO BATTERY  ▶")
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

    # ── slots ─────────────────────────────────────────────────────────
    def _on_progress(self, pct: int) -> None:
        self._ring.set_value(pct)
        self._ring.set_caption(f"{pct}%", "SWEEPING")

    def _on_component(self, key: str, data: dict) -> None:
        if key == "specs":
            self._sys_body.setText(self._fmt_specs(data))
        elif key == "storage":
            self._stor_body.setText(self._fmt_storage(data))
            self.status.emit("storage", self._storage_state(data))
        elif key == "battery":
            self._batt_body.setText(self._fmt_battery(data))
            self.status.emit("battery", self._battery_state(data))

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
            poh = dr.get("power_on_hours")
            tbw = dr.get("host_writes_tb")
            out += self._row("DRIVE", f"{dr.get('model', '?')}")
            out += self._row("", f"health: {dr.get('health', '?')}")
            out += self._row("", f"on-hours: "
                                f"{poh if poh is not None else 'n/a'}")
            out += self._row("", f"written: "
                                f"{f'{tbw:.1f} TB' if tbw is not None else 'n/a'}")
        if not d.get("smart_readable", False):
            out += (f"<span style='color:{config.COL_AMBER}'>"
                    f"· run elevated for full S.M.A.R.T.</span>")
        return out

    def _fmt_battery(self, d: dict) -> str:
        if not d.get("present"):
            return "· no battery (desktop)"
        cycles = d.get("cycles")
        cycles_str = str(cycles) if cycles is not None else "n/a"
        return (self._row("WEAR", f"{d.get('wear_pct', '?')}%")
                + self._row("DESIGN", f"{d.get('design_mwh', '?')} mWh")
                + self._row("FULL", f"{d.get('full_mwh', '?')} mWh")
                + self._row("CYCLES", cycles_str))

    # ── status mapping for the indicator lights ───────────────────────
    @staticmethod
    def _storage_state(d: dict) -> str:
        for dr in d.get("drives", []):
            if dr.get("health") == "FAILING":
                return "fail"
        return "pass"

    @staticmethod
    def _battery_state(d: dict) -> str:
        if not d.get("present"):
            return "idle"
        wear = d.get("wear_pct")
        if wear is None:
            return "warn"
        if wear >= config.BATTERY_WEAR_FAIL_PCT:
            return "fail"
        if wear >= config.BATTERY_WEAR_WARN_PCT:
            return "warn"
        return "pass"
