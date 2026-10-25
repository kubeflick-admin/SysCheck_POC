"""Boot / splash view — a terminal-initialisation sequence then a single
big 'INITIATE DIAGNOSTIC' action. Without elevation it opens with a
banner asking to run as Administrator."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QWidget

from ... import config
from ...telemetry.smart_sources.smartctl import is_admin
from ..widgets import TelemetryConsole
from ._common import label, panel

_BOOT_LINES = [
    "SYSCHECK KERNEL v0.1.0 — initialising ...",
    "loading telemetry drivers .............. OK",
    "mounting WMI providers ................. OK",
    "linking opencv vision pipeline ......... OK",
    "arming thermal load generator .......... OK",
    "calibrating sensor bus ................. OK",
    "",
    "READY. Awaiting operator authorisation.",
]


class SplashView(QWidget):
    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addWidget(label(config.APP_TITLE, "h1"))
        root.addWidget(label(config.APP_TAGLINE, "dim"))

        self._admin_banner = label(config.ADMIN_BANNER)
        self._admin_banner.setWordWrap(True)
        self._admin_banner.setStyleSheet(
            f"color:{config.COL_AMBER}; border:1px solid {config.COL_AMBER};"
            f" padding:10px 14px; font-weight:bold;")
        self._admin_banner.setVisible(not is_admin())
        root.addWidget(self._admin_banner)

        box = panel(accent=True)
        box_l = QVBoxLayout(box)
        self._console = TelemetryConsole(max_lines=60)
        self._console.setMinimumHeight(260)
        box_l.addWidget(self._console)
        root.addWidget(box, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn = QPushButton("INITIATE DIAGNOSTIC  ▶")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self.start_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

        self._lines = list(_BOOT_LINES)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_line)

    def play_boot(self) -> None:
        """(Re)play the boot sequence."""
        self._console.clear()
        self._btn.setEnabled(False)
        self._lines = list(_BOOT_LINES)
        self._timer.start(180)

    def _next_line(self) -> None:
        if not self._lines:
            self._timer.stop()
            self._btn.setEnabled(True)
            return
        self._console.log(self._lines.pop(0))
