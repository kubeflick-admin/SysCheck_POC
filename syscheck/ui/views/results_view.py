"""Results view — the high-impact A/B/C/F verdict plus a plain-language
summary card per subsystem, written for a non-technical buyer. The whole
scan can be exported from here as a single HTML report."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QVBoxLayout,
                               QHBoxLayout, QPushButton, QWidget, QScrollArea)

from ... import config
from ...export import computer_name, default_report_name, render_html_report
from ...scoring import (STATUS_WORDS, ComponentScore, build_report,
                        headline_for, subhead_for)
from ...telemetry.smart_sources.smartctl import is_admin
from ..widgets import CircularProgress
from ._common import label, panel, hline

_GRADE_COLOR = {"A": config.COL_GREEN, "B": config.COL_BLUE,
                "C": config.COL_AMBER, "F": config.COL_RED,
                "N/A": config.COL_TEXT_DIM}
_STATUS_COLOR = {"pass": config.COL_GREEN, "warn": config.COL_AMBER,
                 "fail": config.COL_RED, "inconclusive": config.COL_AMBER,
                 "skip": config.COL_TEXT_DIM}


class ResultsView(QWidget):
    restart_requested = Signal()
    log = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: Dict[str, object] = {}
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

        # Where the last export went (or why it failed); hidden until then.
        self._saved = label("", "dim")
        self._saved.setWordWrap(True)
        self._saved.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._saved.setVisible(False)
        root.addWidget(self._saved)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._hide_serials = QCheckBox("HIDE SERIAL NUMBERS")
        self._hide_serials.setToolTip(
            "Replace drive serial numbers with \"Hidden\" in the report.")
        btn_row.addWidget(self._hide_serials)
        self._export = QPushButton("EXPORT REPORT  ⤓")
        self._export.clicked.connect(self._export_report)
        btn_row.addWidget(self._export)
        self._restart = QPushButton("RUN AGAIN  ↻")
        self._restart.clicked.connect(self.restart_requested.emit)
        btn_row.addWidget(self._restart)
        root.addLayout(btn_row)

    # ── populate ──────────────────────────────────────────────────────
    def set_results(self, results: Dict[str, object]) -> None:
        self._results = results
        self._show_saved("")
        report = build_report(results)
        overall = report["overall"]
        grade = overall["grade"]
        color = _GRADE_COLOR.get(grade, config.COL_TEXT)

        score = overall["score"]
        self._grade_ring.set_color(color)
        self._grade_ring.set_value(score or 0)
        self._grade_ring.set_caption(
            grade, f"{score:.0f}/100" if score is not None else "NO DATA")

        self._headline.setText(headline_for(grade))
        self._headline.setStyleSheet(f"color:{color};")
        self._subhead.setText(subhead_for(report["components"]))

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
        badge = label(STATUS_WORDS.get(c.status, c.status.upper()))
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

    # ── export ────────────────────────────────────────────────────────
    def _export_report(self) -> None:
        """Ask where to save, then write the scan as one HTML file."""
        now = datetime.now()
        computer = computer_name()
        folder = (QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation) or str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SysCheck report",
            str(Path(folder) / default_report_name(computer, now)),
            "HTML report (*.html)")
        if not path:
            return                      # cancelled
        if not path.lower().endswith((".html", ".htm")):
            path += ".html"
        html = render_html_report(
            self._results, computer=computer, generated=now,
            hide_serials=self._hide_serials.isChecked(), admin=is_admin())
        try:
            Path(path).write_text(html, encoding="utf-8")
        except OSError as exc:
            self._show_saved(f"Could not save the report: {exc}",
                             config.COL_RED)
            self.log.emit(f"[REPORT] Save failed: {exc}")
            return
        self._show_saved(f"Report saved to {path}")
        self.log.emit(f"[REPORT] Saved {path}")

    def _show_saved(self, text: str, color: str = "") -> None:
        self._saved.setStyleSheet(f"color:{color};" if color else "")
        self._saved.setText(text)
        self._saved.setVisible(bool(text))

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
