"""Touchpad continuity view.

A large capture surface the operator sweeps the cursor across: each grid
cell lights as the pointer reaches it (exposing dead patches a pad might
have), while left/right/double click and two-finger scroll are ticked off
as they are demonstrated. Backed by the Qt-free :class:`TouchpadState`.
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QWidget)

from ... import config
from ...diagnostics.touchpad import TouchpadState, GESTURES
from ...scoring import score_touchpad
from ..widgets import CircularProgress
from ._common import label, light_state, panel

_GESTURE_LABELS = dict(GESTURES)


class TouchpadPad(QWidget):
    """Custom capture surface; reports coverage + gestures to the view."""

    covered = Signal()                 # a new cell was lit
    gesture = Signal(str)              # a gesture was demonstrated

    def __init__(self, state: TouchpadState, parent=None):
        super().__init__(parent)
        self._state = state
        self.setMinimumHeight(300)
        self.setMouseTracking(True)     # move events without a button held
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)

    def set_state(self, state: TouchpadState) -> None:
        self._state = state
        self.update()

    # ── input capture ──────────────────────────────────────────────────
    def mouseMoveEvent(self, e):  # noqa: N802
        w, h = max(1, self.width()), max(1, self.height())
        if self._state.visit(e.position().x() / w, e.position().y() / h):
            self.covered.emit()
            self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self._state.mark("left_click"):
            self.gesture.emit("left_click")
        elif e.button() == Qt.RightButton and self._state.mark("right_click"):
            self.gesture.emit("right_click")

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self._state.mark("double_click"):
            self.gesture.emit("double_click")

    def wheelEvent(self, e):  # noqa: N802
        if self._state.mark("scroll"):
            self.gesture.emit("scroll")

    # ── paint ───────────────────────────────────────────────────────────
    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#070808"))

        cols, rows = config.TOUCHPAD_GRID_COLS, config.TOUCHPAD_GRID_ROWS
        cw, ch = w / cols, h / rows
        for c in range(cols):
            for r in range(rows):
                cell = QRectF(c * cw + 1, r * ch + 1, cw - 2, ch - 2)
                if self._state.is_visited(c, r):
                    col = QColor(config.COL_GREEN)
                    col.setAlphaF(0.30)
                    p.fillRect(cell, col)
                    p.setPen(QPen(QColor(config.COL_GREEN), 1))
                else:
                    p.setPen(QPen(QColor(config.COL_GRID), 1))
                p.drawRect(cell)
        p.end()


class TouchpadView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = TouchpadState(config.TOUCHPAD_GRID_COLS,
                                    config.TOUCHPAD_GRID_ROWS)
        self._result: Dict[str, object] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label("TOUCHPAD CONTINUITY TEST", "h2"))
        root.addWidget(label(
            "Glide the cursor into every corner of the pad below — each tile "
            "lights as the pointer reaches it. Then demonstrate each gesture "
            "once.", "dim"))

        head = QHBoxLayout()
        self._ring = CircularProgress(diameter=120)
        self._ring.set_caption("0%", "AREA")
        head.addWidget(self._ring, 0, Qt.AlignTop)
        self._prompt = label("", "mono")
        self._prompt.setWordWrap(True)
        head.addWidget(self._prompt, 1, Qt.AlignTop)
        root.addLayout(head)

        pad_panel = panel(accent=True)
        pl = QVBoxLayout(pad_panel)
        self._pad = TouchpadPad(self._state)
        self._pad.covered.connect(self._on_covered)
        self._pad.gesture.connect(self._on_gesture)
        pl.addWidget(self._pad)
        root.addWidget(pad_panel, 1)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._btn = QPushButton("PROCEED TO PORTS  ▶")
        self._btn.clicked.connect(self._finish)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        self._state = TouchpadState(config.TOUCHPAD_GRID_COLS,
                                    config.TOUCHPAD_GRID_ROWS)
        self._pad.set_state(self._state)
        self._result = {}
        self.status.emit("touchpad", "active")
        self._update_prompt()

    # ── slots ────────────────────────────────────────────────────────────
    def _on_covered(self) -> None:
        self._update_prompt()
        if self._state.all_done():
            self.log.emit("[PAD] Full coverage and all gestures registered.")

    def _on_gesture(self, name: str) -> None:
        self.log.emit(f"[PAD] Gesture OK: {_GESTURE_LABELS.get(name, name)}.")
        self._update_prompt()

    def _update_prompt(self) -> None:
        cov = self._state.coverage()
        self._ring.set_value(cov * 100)
        self._ring.set_caption(f"{cov*100:.0f}%", "AREA")
        self._ring.set_color(config.COL_GREEN if cov >= 1.0 else config.COL_BLUE)

        pending = [_GESTURE_LABELS[g] for g in self._state.pending_gestures()]
        lines = [f"Coverage {cov*100:.0f}%  "
                 f"({self._state.covered_cells()}/{self._state.total_cells()} "
                 f"tiles)."]
        if pending:
            lines.append("Gestures left: " + ", ".join(pending))
        else:
            lines.append("✓ All gestures demonstrated.")
        if self._state.all_done():
            lines.append("✓ Ready — proceed when you like.")
        self._prompt.setText("\n".join(lines))

    # ── finish ───────────────────────────────────────────────────────────
    def _finish(self) -> None:
        summary = self._state.summary()
        self._result = summary
        cov = summary["coverage_pct"]
        missing = summary["missing_gestures"]
        self.status.emit("touchpad",
                         light_state(score_touchpad(summary).status))
        self.log.emit(f"[PAD] Coverage {cov}% — "
                      f"{len(missing)} gesture(s) not demonstrated.")
        self.finished.emit(self._result)
        self.next_requested.emit()

    def _skip(self) -> None:
        self._result = {"skipped": True}
        self.status.emit("touchpad", "idle")
        self.finished.emit(self._result)
        self.next_requested.emit()
