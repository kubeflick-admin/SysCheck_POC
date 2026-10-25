"""Storage view — a full health page per drive, in the style of dedicated
disk-health tools.

Each drive gets a large Health % bar with its condition word, the
plain-English summary, the estimated remaining life, its details (model,
serial, firmware, interface, size, temperatures, power-on time, data
written / read) and its full S.M.A.R.T. table. Several drives get a
selector row. Everything comes from the telemetry sweep; this step never
touches the hardware.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QGridLayout,
                               QHBoxLayout, QHeaderView, QPushButton,
                               QScrollArea, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ... import config
from ...diskhealth.describe import (ATA_COLUMNS, SOURCE_LABELS,
                                    DriveAssessment, SmartRow, assess,
                                    condition, detail_rows, no_table_reason,
                                    size_words, smart_columns, smart_rows,
                                    unsafe_shutdowns_note)
from ..widgets import HealthBar
from ._common import health_color, hline, label, panel

SEVERITY_COLOR = {"ok": config.COL_GREEN, "info": config.COL_BLUE,
                  "warn": config.COL_AMBER, "fail": config.COL_RED}
_ROW_HEIGHT = 24


class StorageView(QWidget):
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addWidget(label("DRIVE HEALTH", "h2"))
        intro = label("Health starts at 100% and drops for every sign of "
                      "damage or wear the drive has recorded. Remaining life "
                      "is an estimate, not a promise.", "dim")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # One selector button per drive (hidden with a single drive).
        self._tabs_host = QWidget()
        self._tabs = QHBoxLayout(self._tabs_host)
        self._tabs.setContentsMargins(0, 0, 0, 0)
        self._tabs.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        root.addWidget(self._tabs_host)

        self._pages = QStackedWidget()
        self._group.idClicked.connect(self._pages.setCurrentIndex)
        root.addWidget(self._pages, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn = QPushButton("PROCEED TO BATTERY  ▶")
        self._btn.clicked.connect(self.next_requested.emit)
        btn_row.addWidget(self._btn)
        root.addLayout(btn_row)

        self.set_storage(None)

    # ── populate ──────────────────────────────────────────────────────
    def set_storage(self, data: Optional[dict]) -> None:
        """Rebuild the page from the telemetry ``storage`` result."""
        self._clear()
        drives = (data or {}).get("drives") or []
        for i, drive in enumerate(drives):
            a = assess(drive)
            self._pages.addWidget(self._scroll(self._drive_page(a)))
            tab = QPushButton(self._tab_text(a))
            tab.setObjectName("tab")
            tab.setCheckable(True)
            self._group.addButton(tab, i)
            self._tabs.addWidget(tab)
        self._tabs.addStretch(1)
        if drives:
            self._group.button(0).setChecked(True)
        else:
            self._pages.addWidget(self._empty_page())
        self._tabs_host.setVisible(len(drives) > 1)
        self._pages.setCurrentIndex(0)

    def _clear(self) -> None:
        for tab in self._group.buttons():
            self._group.removeButton(tab)
        while self._tabs.count():
            w = self._tabs.takeAt(0).widget()
            if w:
                w.deleteLater()
        while self._pages.count():
            w = self._pages.widget(0)
            self._pages.removeWidget(w)
            w.deleteLater()

    @staticmethod
    def _tab_text(a: DriveAssessment) -> str:
        number = (a.drive.get("raw") or {}).get("disk_number")
        pct = a.health.health_pct
        parts = [f"DISK {number}" if number is not None else None,
                 str(a.drive.get("kind") or "Drive"),
                 size_words(a.drive.get("size_gb")),
                 f"{pct}%" if pct is not None else "N/A"]
        return "  ·  ".join(p for p in parts if p)

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _empty_page() -> QWidget:
        card = panel()
        lay = QVBoxLayout(card)
        lay.addWidget(label("NO DRIVES LISTED", "h2"))
        msg = label("Windows didn't list any drives and smartctl couldn't "
                    "read one. Run SysCheck as Administrator and scan "
                    "again.", "dim")
        msg.setWordWrap(True)
        lay.addWidget(msg)
        lay.addStretch(1)
        return card

    # ── one drive ─────────────────────────────────────────────────────
    def _drive_page(self, a: DriveAssessment) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)
        lay.addWidget(self._overview(a))
        lay.addWidget(self._details(a))
        lay.addWidget(self._smart(a))
        lay.addStretch(1)
        return page

    @staticmethod
    def _overview(a: DriveAssessment) -> QWidget:
        pct = a.health.health_pct
        color = health_color(pct)
        card = panel(accent=True)
        lay = QVBoxLayout(card)

        head = QHBoxLayout()
        title = label(a.name.upper(), "h2")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        head.addWidget(label(f"{a.drive.get('kind')} · "
                             f"{size_words(a.drive.get('size_gb'))}", "dim"),
                       0, Qt.AlignTop)
        lay.addLayout(head)
        lay.addWidget(hline())

        row = QHBoxLayout()
        row.addWidget(label("HEALTH", "dim"))
        bar = HealthBar()
        bar.set_value(pct, color)
        row.addWidget(bar, 1)
        word = label(condition(pct))
        word.setMinimumWidth(110)
        word.setStyleSheet(f"color:{color}; font-size:16px; "
                           f"font-weight:bold; letter-spacing:1px;")
        row.addWidget(word)
        lay.addLayout(row)

        summary = label(a.summary)
        summary.setWordWrap(True)
        lay.addWidget(summary)

        life_row = QHBoxLayout()
        life_row.addWidget(label("ESTIMATED REMAINING LIFE", "dim"))
        text = a.life.text + (" (rough: power-on time estimated)"
                              if a.life.rough else "")
        life = label(text)
        life_color = (config.COL_RED if a.life.replace_soon
                      else config.COL_TEXT_DIM if a.life.days is None
                      else config.COL_TEXT)
        life.setStyleSheet(f"color:{life_color}; font-weight:bold;")
        life_row.addWidget(life)
        life_row.addStretch(1)
        lay.addLayout(life_row)
        return card

    @staticmethod
    def _details(a: DriveAssessment) -> QWidget:
        card = panel()
        lay = QVBoxLayout(card)
        lay.addWidget(label("DRIVE DETAILS", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        rows = detail_rows(a.drive)
        half = (len(rows) + 1) // 2
        for i, (key, value) in enumerate(rows):
            col, r = (0, i) if i < half else (2, i - half)
            grid.addWidget(label(key.upper(), "dim"), r, col)
            val = label(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(val, r, col + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        note = unsafe_shutdowns_note(a.drive)
        if note:
            hint = label(note, "dim")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        return card

    def _smart(self, a: DriveAssessment) -> QWidget:
        card = panel()
        lay = QVBoxLayout(card)
        head = QHBoxLayout()
        head.addWidget(label("S.M.A.R.T. ATTRIBUTES", "h2"))
        head.addStretch(1)
        head.addWidget(label("read by " + SOURCE_LABELS.get(
            a.drive.get("source"), "?"), "dim"))
        lay.addLayout(head)

        rows = smart_rows(a.drive)
        if rows:
            lay.addWidget(self._smart_table(smart_columns(a.drive), rows))
        else:
            msg = label(no_table_reason(a.drive), "dim")
            msg.setWordWrap(True)
            lay.addWidget(msg)

        lay.addWidget(label("HOW THE HEALTH % WAS WORKED OUT", "dim"))
        how = label("\n".join(a.health.technical()), "mono")
        how.setTextFormat(Qt.PlainText)
        how.setWordWrap(True)
        lay.addWidget(how)
        return card

    @staticmethod
    def _smart_table(columns: Tuple[str, ...],
                     rows: List[SmartRow]) -> QTableWidget:
        table = QTableWidget(len(rows), len(columns))
        table.setObjectName("smart")
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        ata = columns == ATA_COLUMNS
        name_col = columns.index("ATTRIBUTE" if ata else "NAME")
        status_col = columns.index("STATUS")
        centred = [columns.index(c) for c in ("ID", "VALUE", "WORST",
                                              "THRESH")] if ata else []
        for r, row in enumerate(rows):
            for c, text in enumerate(row.cells(columns)):
                item = QTableWidgetItem(text)
                if c in centred:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, c, item)
            color = QColor(SEVERITY_COLOR[row.severity])
            table.item(r, status_col).setForeground(color)
            if row.severity in ("warn", "fail"):
                table.item(r, name_col).setForeground(color)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(name_col, QHeaderView.Stretch)
        header.setHighlightSections(False)
        # The page scrolls, not the table: show every row.
        table.setFixedHeight(header.sizeHint().height()
                             + _ROW_HEIGHT * len(rows)
                             + 2 * table.frameWidth() + 2)
        return table
