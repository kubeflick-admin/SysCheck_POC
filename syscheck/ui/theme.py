"""Global Qt Style Sheet (QSS) for the cyber-HUD look.

Colours are injected from :mod:`syscheck.config` so the whole palette can
be retuned in one place. ``build_stylesheet()`` returns a string ready for
``QApplication.setStyleSheet``.
"""

from __future__ import annotations

from .. import config


def build_stylesheet() -> str:
    c = config
    return f"""
    * {{
        font-family: "{c.MONO_FONT}", "Courier New", monospace;
        color: {c.COL_TEXT};
        selection-background-color: {c.COL_BLUE};
        selection-color: {c.COL_BG};
    }}

    QWidget#root, QMainWindow {{
        background-color: {c.COL_BG};
    }}
    /* Scroll areas would paint the light system palette between cards. */
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}

    /* Raised panels / cards */
    QFrame#panel {{
        background-color: {c.COL_PANEL};
        border: 1px solid {c.COL_GRID};
        border-radius: 6px;
    }}
    QFrame#panelAccent {{
        background-color: {c.COL_PANEL};
        border: 1px solid {c.COL_BLUE};
        border-radius: 6px;
    }}

    /* Headings */
    QLabel#h1 {{
        color: {c.COL_BLUE};
        font-size: 26px;
        font-weight: bold;
        letter-spacing: 2px;
    }}
    QLabel#h2 {{
        color: {c.COL_TEXT};
        font-size: 16px;
        font-weight: bold;
        letter-spacing: 1px;
    }}
    QLabel#dim {{
        color: {c.COL_TEXT_DIM};
        font-size: 12px;
    }}
    QLabel#mono {{
        color: {c.COL_GREEN};
        font-size: 12px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: transparent;
        border: 1px solid {c.COL_BLUE};
        color: {c.COL_BLUE};
        padding: 9px 22px;
        border-radius: 4px;
        font-weight: bold;
        letter-spacing: 1px;
    }}
    QPushButton:hover {{
        background-color: {c.COL_BLUE};
        color: {c.COL_BG};
    }}
    QPushButton:disabled {{
        border-color: {c.COL_GRID};
        color: {c.COL_TEXT_DIM};
    }}
    QPushButton#ghost {{
        border-color: {c.COL_GRID};
        color: {c.COL_TEXT_DIM};
    }}
    QPushButton#ghost:hover {{
        border-color: {c.COL_TEXT};
        background-color: {c.COL_PANEL_HI};
        color: {c.COL_TEXT};
    }}

    /* Drive selector on the storage page */
    QPushButton#tab {{
        border-color: {c.COL_GRID};
        color: {c.COL_TEXT_DIM};
        padding: 6px 16px;
    }}
    QPushButton#tab:hover {{
        background-color: {c.COL_PANEL_HI};
        color: {c.COL_TEXT};
    }}
    QPushButton#tab:checked {{
        border-color: {c.COL_BLUE};
        background-color: {c.COL_BLUE};
        color: {c.COL_BG};
    }}

    /* S.M.A.R.T. attribute table */
    QTableWidget#smart {{
        background-color: #070808;
        alternate-background-color: {c.COL_PANEL};
        border: 1px solid {c.COL_GRID};
        border-radius: 4px;
        gridline-color: {c.COL_GRID};
        font-size: 12px;
    }}
    QTableWidget#smart::item {{
        padding: 0 6px;
    }}
    QHeaderView::section {{
        background-color: {c.COL_PANEL_HI};
        color: {c.COL_BLUE};
        border: none;
        border-bottom: 1px solid {c.COL_GRID};
        padding: 4px 6px;
        font-weight: bold;
        letter-spacing: 1px;
    }}

    /* Telemetry console */
    QPlainTextEdit#console {{
        background-color: #070808;
        border: 1px solid {c.COL_GRID};
        border-radius: 4px;
        color: {c.COL_GREEN};
        font-size: 12px;
        padding: 6px;
    }}

    /* Progress bar (fallback / inline) */
    QProgressBar {{
        background-color: {c.COL_PANEL};
        border: 1px solid {c.COL_GRID};
        border-radius: 3px;
        text-align: center;
        color: {c.COL_TEXT};
        height: 16px;
    }}
    QProgressBar::chunk {{
        background-color: {c.COL_BLUE};
        border-radius: 2px;
    }}

    QScrollBar:vertical {{
        background: {c.COL_BG};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c.COL_GRID};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c.COL_BLUE}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

    QToolTip {{
        background-color: {c.COL_PANEL};
        color: {c.COL_TEXT};
        border: 1px solid {c.COL_BLUE};
    }}
    """
