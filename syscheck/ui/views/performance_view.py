"""Performance & hang check view.

Explains the check and the one temporary file it writes, then waits for
START: the run takes about two minutes and the laptop should be left
alone meanwhile. :class:`PerformanceWorker` reports each stage live; the
result shows the Performance score and label, why the laptop might hang
(most likely cause first, each with a fix) and a typical-use guide.
"""

from __future__ import annotations

from html import escape
from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ... import config
from ...perf.describe import assess
from ...perf.score import USE_NO, USE_OK
from ...scoring import score_performance
from ...telemetry.performance import STAGES
from ...workers import PerformanceWorker
from ..widgets import CircularProgress
from ._common import label, light_state, panel

_STATUS_COLOR = {"pass": config.COL_GREEN, "warn": config.COL_AMBER,
                 "fail": config.COL_RED, "inconclusive": config.COL_AMBER,
                 "skip": config.COL_TEXT_DIM}
_USE_COLOR = {USE_OK: config.COL_GREEN, USE_NO: config.COL_RED}


class PerformanceView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[PerformanceWorker] = None
        self._result: Dict[str, object] = {}
        self._context: Dict[str, object] = {}    # the scan so far
        self._current: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        self._ring = CircularProgress(diameter=150)
        top.addWidget(self._ring, 0, Qt.AlignTop)
        intro = QVBoxLayout()
        intro.addWidget(label("PERFORMANCE & HANG CHECK", "h2"))
        desc = label("Measures how fast this laptop is and looks for what "
                     "makes it hang or feel slow. It takes about 2 minutes; "
                     "please leave the laptop alone while it runs.", "dim")
        desc.setWordWrap(True)
        intro.addWidget(desc)
        notice = label(f"The drive test writes one {config.PERF_DISK_TEST_MB}"
                       " MB temporary file in your Temp folder and deletes "
                       "it straight afterwards. Nothing else on the laptop "
                       "is changed.", "mono")
        notice.setWordWrap(True)
        notice.setStyleSheet(f"color:{config.COL_AMBER};")
        intro.addWidget(notice)
        intro.addStretch(1)
        top.addLayout(intro, 1)
        root.addLayout(top)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(12)
        stages = panel(accent=True)
        sl = QVBoxLayout(stages)
        sl.addWidget(label("STAGES", "dim"))
        self._stage_lines = {}
        for key, title, _ in STAGES:
            line = label("", "mono")
            line.setWordWrap(True)
            self._stage_lines[key] = (line, title)
            sl.addWidget(line)
        bl.addWidget(stages)

        self._result_card = panel()
        rl = QVBoxLayout(self._result_card)
        self._headline = label("", "h2")
        self._headline.setWordWrap(True)
        rl.addWidget(self._headline)
        # Hang causes and the use guide, only for a completed run.
        self._details = QWidget()
        dl = QVBoxLayout(self._details)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(label("WHY IT MIGHT HANG", "dim"))
        self._hangs = label("", "")
        self._hangs.setWordWrap(True)
        self._hangs.setTextFormat(Qt.RichText)
        dl.addWidget(self._hangs)
        dl.addWidget(label("TYPICAL USE", "dim"))
        self._uses = QGridLayout()
        self._uses.setHorizontalSpacing(18)
        dl.addLayout(self._uses)
        rl.addWidget(self._details)
        bl.addWidget(self._result_card)
        bl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip_or_stop)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._run_btn = QPushButton("START CHECK  ▶")
        self._run_btn.clicked.connect(self._begin)
        btn_row.addWidget(self._run_btn)
        self._btn = QPushButton("PROCEED TO VERDICT  ▶")
        self._btn.clicked.connect(self.next_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)
        self.start()

    # ── context injection ──────────────────────────────────────────────
    def set_context(self, results: Optional[Dict[str, object]]) -> None:
        """The scan so far: drive health, drive types, thermal throttling
        and the graphics card feed the hang causes and the use guide."""
        self._context = dict(results or {})

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        """Show the notice and wait for START (the check doesn't run on
        entering the step)."""
        self._result = {}
        self._current = None
        self._ring.set_value(0)
        self._ring.set_color(config.COL_BLUE)
        self._ring.set_caption("READY", "PERFORMANCE")
        for line, title in self._stage_lines.values():
            line.setText(f"○ {title}")
            line.setStyleSheet(f"color:{config.COL_TEXT_DIM};")
        self._result_card.setVisible(False)
        self._run_btn.setVisible(True)
        self._run_btn.setEnabled(True)
        self._skip_btn.setText("SKIP")
        self._skip_btn.setEnabled(True)
        self._btn.setVisible(False)

    def _begin(self) -> None:
        self._run_btn.setEnabled(False)
        self._skip_btn.setText("STOP")
        self._ring.set_color(config.COL_AMBER)
        self.status.emit("performance", "active")
        self._worker = PerformanceWorker()
        self._worker.log.connect(self.log.emit)
        self._worker.stage.connect(self._on_stage)
        self._worker.sample.connect(self._on_sample)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_perf.connect(self._on_finished)
        self._worker.start()

    def _running(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _skip_or_stop(self) -> None:
        if self._running():
            self._worker.cancel()        # the result arrives, not completed
            self._skip_btn.setEnabled(False)
            return
        self._result = {"skipped": True}
        self.status.emit("performance", "idle")
        self.finished.emit(self._result)
        self.next_requested.emit()

    def stop_worker(self) -> None:
        # Stages check for cancel often; an event-log read can take a while.
        if self._running():
            self._worker.cancel()
            self._worker.wait(20000)

    # ── slots ────────────────────────────────────────────────────────────
    def _set_line(self, key: str, mark: str, extra: str = "",
                  color: str = config.COL_TEXT) -> None:
        line, title = self._stage_lines[key]
        line.setText(f"{mark} {title}" + (f" — {extra}" if extra else ""))
        line.setStyleSheet(f"color:{color};")

    def _on_stage(self, key: str, _title: str) -> None:
        if self._current:
            self._set_line(self._current, "✓", color=config.COL_GREEN)
        self._current = key
        self._set_line(key, "▸", "running …", config.COL_BLUE)

    def _on_sample(self, key: str, data: dict) -> None:
        if key not in self._stage_lines:
            return
        parts = []
        if isinstance(data.get("elapsed"), (int, float)):
            parts.append(f"{data['elapsed']:.0f}s")
        if data.get("cpu_pct") is not None:
            parts.append(f"processor {data['cpu_pct']:.0f}%")
        if data.get("ram_pct") is not None:
            parts.append(f"memory {data['ram_pct']:.0f}%")
        if data.get("phase"):
            phase = {"single": "one core", "multi": "all cores"}.get(
                data["phase"], data["phase"])
            parts.append(phase + (f" {data['pct']:.0f}%"
                                  if data.get("pct") is not None else ""))
        if data.get("mhz"):
            parts.append(f"{data['mhz'] / 1000:.1f} GHz")
        self._set_line(key, "▸", " · ".join(parts) or "running …",
                       config.COL_BLUE)

    def _on_progress(self, pct: int) -> None:
        self._ring.set_value(pct)
        self._ring.set_caption(f"{pct}%", "MEASURING")

    def _on_finished(self, result: dict) -> None:
        self._result = result
        completed = result.get("completed", False)
        if self._current:
            self._set_line(self._current, "✓" if completed else "■",
                           "" if completed else "stopped",
                           config.COL_GREEN if completed
                           else config.COL_AMBER)
        comp = score_performance(result, self._context)
        color = _STATUS_COLOR.get(comp.status, config.COL_TEXT)
        self._ring.set_value(100)
        self._ring.set_color(color)
        self._ring.set_caption(f"{comp.score:.0f}" if comp.status not in (
            "skip", "inconclusive") else "—", "SCORE")
        self._show_result(result, comp.note, color)
        self._run_btn.setVisible(False)
        self._skip_btn.setEnabled(False)
        self._btn.setVisible(True)
        self.status.emit("performance", light_state(comp.status))
        self.finished.emit(result)

    def _show_result(self, result: dict, headline: str, color: str) -> None:
        self._headline.setText(headline)
        self._headline.setStyleSheet(f"color:{color};")
        a = assess(result, self._context)
        if a.hangs:
            items = "".join(
                f"<p style='margin:0 0 6px 0'>· {escape(h.cause)}<br>"
                f"<span style='color:{config.COL_TEXT_DIM}'>Fix: "
                f"{escape(h.fix)}</span></p>" for h in a.hangs)
        else:
            items = (f"<span style='color:{config.COL_GREEN}'>Nothing found "
                     "that should make it hang.</span>")
        self._hangs.setText(items)
        while self._uses.count():
            w = self._uses.takeAt(0).widget()
            if w:
                w.deleteLater()
        for row, use in enumerate(a.uses):
            verdict = use["verdict"] or "n/a"
            self._uses.addWidget(label(use["use"].upper(), "dim"), row, 0)
            value = label(verdict + (f" — {use['note']}" if use["note"]
                                     else ""))
            value.setStyleSheet(
                f"color:{_USE_COLOR.get(verdict, config.COL_AMBER)};"
                f"font-weight:bold;")
            self._uses.addWidget(value, row, 1)
        self._uses.setColumnStretch(1, 1)
        self._details.setVisible(bool(result.get("completed")))
        self._result_card.setVisible(True)
