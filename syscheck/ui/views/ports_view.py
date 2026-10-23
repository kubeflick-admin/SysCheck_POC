"""Ports / peripherals view.

The OS can't confirm an empty port is sound, so this is a live check: the
operator plugs a device into each port in turn and we report every USB
arrival and removal in real time (via :class:`PortsWorker`), alongside a
starting inventory of attached USB devices and detected display outputs.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QWidget)

from ... import config
from ...workers import PortsWorker
from ..widgets import CircularProgress
from ._common import label, panel


class PortsView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[PortsWorker] = None
        self._result: Dict[str, object] = {}
        self._added = 0
        self._removed = 0
        self._displays: List[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label("PORTS & PERIPHERALS TEST", "h2"))
        root.addWidget(label(
            "Plug a device (USB stick, dongle, cable) into each port one at a "
            "time, then unplug it. Every connect and disconnect is detected "
            "live — exercise all of your ports to confirm they work.", "dim"))

        head = QHBoxLayout()
        self._ring = CircularProgress(diameter=120)
        self._ring.set_caption("0", "EVENTS")
        head.addWidget(self._ring, 0, Qt.AlignTop)
        self._counts = label("", "mono")
        self._counts.setWordWrap(True)
        head.addWidget(self._counts, 1, Qt.AlignTop)
        root.addLayout(head)

        body = QHBoxLayout()
        inv_panel = panel()
        il = QVBoxLayout(inv_panel)
        il.addWidget(label("ATTACHED NOW", "dim"))
        self._inv = label("· scanning …", "mono")
        self._inv.setWordWrap(True)
        self._inv.setTextFormat(Qt.RichText)
        self._inv.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        il.addWidget(self._inv, 1)
        body.addWidget(inv_panel, 1)

        ev_panel = panel(accent=True)
        el = QVBoxLayout(ev_panel)
        el.addWidget(label("LIVE PORT ACTIVITY", "dim"))
        self._events = label("· waiting for a device to be plugged in …", "mono")
        self._events.setWordWrap(True)
        self._events.setTextFormat(Qt.RichText)
        self._events.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        el.addWidget(self._events, 1)
        body.addWidget(ev_panel, 1)
        root.addLayout(body, 1)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._btn = QPushButton("FINISH & SCORE  ▶")
        self._btn.clicked.connect(self._finish)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        self._result = {}
        self._added = 0
        self._removed = 0
        self._displays = []
        self._event_lines: List[str] = []
        self._inv.setText("· scanning …")
        self._events.setText("· waiting for a device to be plugged in …")
        self._ring.set_value(0)
        self._ring.set_color(config.COL_BLUE)
        self._ring.set_caption("0", "EVENTS")
        self._counts.setText("Connects: 0   Disconnects: 0")
        self.status.emit("ports", "active")

        self._worker = PortsWorker()
        self._worker.log.connect(self.log.emit)
        self._worker.inventory.connect(self._on_inventory)
        self._worker.event.connect(self._on_event)
        self._worker.counts.connect(self._on_counts)
        self._worker.start()

    def stop_worker(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(1500)

    # ── slots ────────────────────────────────────────────────────────────
    def _on_inventory(self, usb: dict, displays: list) -> None:
        self._displays = list(displays)
        rows = ""
        for name in list(usb.values())[:12]:
            rows += self._bullet(name, config.COL_TEXT)
        extra = len(usb) - 12
        if extra > 0:
            rows += self._bullet(f"… and {extra} more", config.COL_TEXT_DIM)
        if not usb:
            rows = self._bullet("no USB devices attached", config.COL_TEXT_DIM)
        rows += (f"<br><span style='color:{config.COL_TEXT_DIM}'>DISPLAY OUT"
                 f"</span><br>")
        if displays:
            for d in displays:
                rows += self._bullet(d, config.COL_BLUE)
        else:
            rows += self._bullet("none detected", config.COL_TEXT_DIM)
        self._inv.setText(rows)

    def _on_event(self, kind: str, name: str) -> None:
        color = config.COL_GREEN if kind == "added" else config.COL_AMBER
        arrow = "＋" if kind == "added" else "－"
        self._event_lines.insert(0, self._bullet(f"{arrow} {name}", color))
        self._events.setText("".join(self._event_lines[:14]))

    def _on_counts(self, added: int, removed: int) -> None:
        self._added, self._removed = added, removed
        total = added + removed
        self._ring.set_value(min(100, total * 20))   # 5 events fills the ring
        self._ring.set_color(config.COL_GREEN if added and removed
                             else config.COL_BLUE)
        self._ring.set_caption(str(total), "EVENTS")
        self._counts.setText(f"Connects: {added}   Disconnects: {removed}")

    # ── formatting ────────────────────────────────────────────────────────
    @staticmethod
    def _bullet(text: str, color: str) -> str:
        return f"<span style='color:{color}'>· {text}</span><br>"

    # ── finish ───────────────────────────────────────────────────────────
    def _finish(self) -> None:
        self.stop_worker()
        verified = self._added > 0 and self._removed > 0
        self._result = {
            "connects": self._added,
            "disconnects": self._removed,
            "verified": verified,
            "displays": self._displays,
        }
        state = "pass" if verified else "warn"
        self.status.emit("ports", state)
        self.log.emit(f"[PORT] {self._added} connect(s) / {self._removed} "
                      f"disconnect(s) observed.")
        self.finished.emit(self._result)
        self.next_requested.emit()

    def _skip(self) -> None:
        self.stop_worker()
        self._result = {"skipped": True}
        self.status.emit("ports", "idle")
        self.finished.emit(self._result)
        self.next_requested.emit()
