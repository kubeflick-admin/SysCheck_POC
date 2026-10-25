"""Shared view helpers: panel frames, titled labels, a step header strip,
and the scoring-status -> indicator-light mapping."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout,
                               QWidget, QSizePolicy)

from ... import config
from ...scoring import _band_from_status

# ComponentScore.status -> IndicatorLight state. "Couldn't verify" is amber.
_LIGHT_STATE = {"pass": "pass", "warn": "warn", "fail": "fail",
                "inconclusive": "warn", "skip": "idle"}
_BAND_COLOR = {"pass": config.COL_GREEN, "warn": config.COL_AMBER,
               "fail": config.COL_RED}


def light_state(status: str) -> str:
    """Indicator-light state for a :mod:`syscheck.scoring` status."""
    return _LIGHT_STATE.get(status, "idle")


def health_color(pct: Optional[float]) -> str:
    """Colour for a drive Health %, on the same bands as the score."""
    if pct is None:
        return config.COL_TEXT_DIM
    return _BAND_COLOR[_band_from_status(pct)]


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
        # Preferred height so a wrapped crumb line gets its room.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._index = label("", "h2")
        self._title = label("", "h1")
        self._crumbs = label("", "dim")
        self._crumbs.setWordWrap(True)      # wraps between steps if narrow
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(self._title)
        col.addWidget(self._crumbs)
        root.addLayout(col, 1)
        root.addWidget(self._index, 0, Qt.AlignTop)
        self.set_step(0)

    def set_step(self, idx: int) -> None:
        self._current = max(0, min(idx, len(self._steps) - 1))
        total = len(self._steps)
        self._index.setText(f">> {self._current + 1:02d} / {total:02d}")
        self._title.setText(self._steps[self._current].upper())
        crumbs = "   ".join(
            (f"●\u00a0{s}" if i == self._current else f"○\u00a0{s}")
            for i, s in enumerate(self._steps)
        )
        self._crumbs.setText(crumbs)
