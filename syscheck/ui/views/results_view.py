"""Results view — the high-impact A/B/C/F verdict plus a plain-language
summary card per subsystem, written for a non-technical buyer."""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QWidget,
                               QScrollArea)

from ... import config
from ...scoring import build_report, ComponentScore
from ..widgets import CircularProgress
from ._common import label, panel, hline

_GRADE_COLOR = {"A": config.COL_GREEN, "B": config.COL_BLUE,
                "C": config.COL_AMBER, "F": config.COL_RED,
                "N/A": config.COL_TEXT_DIM}
_STATUS_COLOR = {"pass": config.COL_GREEN, "warn": config.COL_AMBER,
                 "fail": config.COL_RED, "inconclusive": config.COL_AMBER,
                 "skip": config.COL_TEXT_DIM}
_STATUS_WORD = {"pass": "PASS", "warn": "CAUTION", "fail": "FAIL",
                "inconclusive": "NOT VERIFIED", "skip": "N/A"}


class ResultsView(QWidget):
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # Verdict banner
        banner = panel(accent=True)
        bl = QHBoxLayout(banner)
        self._grade_ring = CircularProgress(diameter=170)
        bl.addWidget(self._grade_ring, 0, Qt.AlignVCenter)
        verdict = QVBoxLayout()
        verdict.addWidget(label("DIAGNOSTIC VERDICT", "dim"))
        self._headline = label("—", "h1")
        verdict.addWidget(self._headline)
        self._subhead = label("", "dim")
        self._subhead.setWordWrap(True)
        verdict.addWidget(self._subhead)
        verdict.addStretch(1)
        bl.addLayout(verdict, 1)
        root.addWidget(banner)

        # Scrollable component cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._cards_host = QWidget()
        self._cards_l = QVBoxLayout(self._cards_host)
        self._cards_l.setSpacing(10)
        self._cards_l.addStretch(1)
        scroll.setWidget(self._cards_host)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._restart = QPushButton("RUN AGAIN  ↻")
        self._restart.clicked.connect(self.restart_requested.emit)
        btn_row.addWidget(self._restart)
        root.addLayout(btn_row)

    # ── populate ──────────────────────────────────────────────────────
    def set_results(self, results: Dict[str, object]) -> None:
        report = build_report(results)
        overall = report["overall"]
        grade = overall["grade"]
        color = _GRADE_COLOR.get(grade, config.COL_TEXT)

        score = overall["score"]
        self._grade_ring.set_color(color)
        self._grade_ring.set_value(score or 0)
        self._grade_ring.set_caption(
            grade, f"{score:.0f}/100" if score is not None else "NO DATA")

        self._headline.setText(self._headline_for(grade))
        self._headline.setStyleSheet(f"color:{color};")
        self._subhead.setText(self._subhead_for(report["components"]))

        self._render_cards(report["components"])

    def _render_cards(self, components: List[ComponentScore]) -> None:
        # Clear previous cards (keep the trailing stretch).
        while self._cards_l.count() > 1:
            item = self._cards_l.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for c in components:
            self._cards_l.insertWidget(self._cards_l.count() - 1,
                                       self._make_card(c))

    def _make_card(self, c: ComponentScore) -> QWidget:
        card = panel()
        lay = QVBoxLayout(card)
        head = QHBoxLayout()
        head.addWidget(label(c.label.upper(), "h2"))
        head.addStretch(1)
        badge = label(_STATUS_WORD.get(c.status, c.status.upper()))
        badge.setStyleSheet(
            f"color:{_STATUS_COLOR.get(c.status, config.COL_TEXT)};"
            f"font-weight:bold;")
        head.addWidget(badge)
        lay.addLayout(head)
        lay.addWidget(hline())

        note = label(c.note, "")
        note.setWordWrap(True)
        lay.addWidget(note)
        for d in c.details:
            line = label(f"· {d}", "dim")
            line.setWordWrap(True)
            lay.addWidget(line)
        if c.technical:
            self._add_technical(lay, c.technical)
        return card

    @staticmethod
    def _add_technical(lay: QVBoxLayout, lines: List[str]) -> None:
        """A collapsed "Technical details" section (attribute IDs etc.)."""
        toggle = QPushButton("▸ TECHNICAL DETAILS")
        toggle.setObjectName("ghost")
        toggle.setCheckable(True)
        body = label("\n".join(lines), "mono")
        body.setTextFormat(Qt.PlainText)
        body.setWordWrap(True)
        body.setVisible(False)

        def show(on: bool) -> None:
            body.setVisible(on)
            toggle.setText(("▾" if on else "▸") + " TECHNICAL DETAILS")

        toggle.toggled.connect(show)
        lay.addWidget(toggle, 0, Qt.AlignLeft)
        lay.addWidget(body)

    # ── copy ──────────────────────────────────────────────────────────
    @staticmethod
    def _headline_for(grade: str) -> str:
        return {
            "A": "GRADE A — EXCELLENT CONDITION",
            "B": "GRADE B — GOOD, MINOR WEAR",
            "C": "GRADE C — USABLE, NEGOTIATE PRICE",
            "F": "GRADE F — SIGNIFICANT ISSUES",
            "N/A": "NO GRADE — NOTHING COULD BE MEASURED",
        }.get(grade, f"GRADE {grade}")

    @staticmethod
    def _subhead_for(components: List[ComponentScore]) -> str:
        fails = [c.label for c in components if c.status == "fail"]
        warns = [c.label for c in components if c.status == "warn"]
        unverified = [c.label for c in components
                      if c.status == "inconclusive"]
        if fails:
            text = "Failing checks: " + ", ".join(fails) + \
                (("; caution on " + ", ".join(warns)) if warns else "")
        elif warns:
            text = "Worth a closer look: " + ", ".join(warns)
        elif unverified:
            text = "Everything that could be tested is within healthy limits."
        else:
            text = "All subsystems within healthy limits."
        if unverified:
            text += " Not verified: " + ", ".join(unverified) + "."
        return text
