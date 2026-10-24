"""Shared view helpers: panel frames, titled labels, a step header strip,
and the scoring-status -> indicator-light mapping."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout,
                               QWidget, QSizePolicy)

from ... import config

# ComponentScore.status -> IndicatorLight state. "Couldn't verify" is amber.
_LIGHT_STATE = {"pass": "pass", "warn": "warn", "fail": "fail",
                "inconclusive": "warn", "skip": "idle"}


def light_state(status: str) -> str:
    """Indicator-light state for a :mod:`syscheck.scoring` status."""
    return _LIGHT_STATE.get(status, "idle")


def panel(accent: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("panelAccent" if accent else "panel")
    f.setFrameShape(QFrame.NoFrame)
    return f


def label(text: str, kind: str = "") -> QLabel:
    """kind: '' | 'h1' | 'h2' | 'dim' | 'mono'"""
    lbl = QLabel(text)
    if kind:
        lbl.setObjectName(kind)
    return lbl


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {config.COL_GRID}; background:{config.COL_GRID};")
    line.setFixedHeight(1)
    return line


class StepHeader(QWidget):
    """A ``>> 02 / 05  THERMAL STRESS`` style step indicator."""

    def __init__(self, steps: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._steps = steps
        self._current = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._index = label("", "h2")
        self._title = label("", "h1")
        self._crumbs = label("", "dim")
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(self._title)
        col.addWidget(self._crumbs)
        root.addLayout(col)
        root.addStretch(1)
        root.addWidget(self._index, 0, Qt.AlignTop)
        self.set_step(0)

    def set_step(self, idx: int) -> None:
        self._current = max(0, min(idx, len(self._steps) - 1))
        total = len(self._steps)
        self._index.setText(f">> {self._current + 1:02d} / {total:02d}")
        self._title.setText(self._steps[self._current].upper())
        crumbs = "   ".join(
            (f"● {s}" if i == self._current else f"○ {s}")
            for i, s in enumerate(self._steps)
        )
        self._crumbs.setText(crumbs)
