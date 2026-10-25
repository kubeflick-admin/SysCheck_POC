"""Keyboard continuity ('piano roll') view.

Renders a laptop keyboard, installs an application-level key event filter,
and lights each cap green as its key is actuated. Keys left dark are
flagged so the user can re-press them to tell a dead key from a missed
swipe. While the test runs the filter swallows every key event, so a
swipe can't press a button (Space) or move focus (Tab).
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, QEvent, QObject, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QWidget,
                               QLabel, QApplication)

from ... import config
from ...diagnostics.keyboard import KEYBOARD_ROWS, KeyboardState
from ...scoring import score_keyboard
from ..widgets import CircularProgress
from ._common import label, light_state, panel


# Qt special-key -> layout label. Printable keys resolve via event.text().
_SPECIAL = {
    Qt.Key_Escape: "Esc", Qt.Key_Delete: "Del", Qt.Key_Backspace: "Back",
    Qt.Key_Tab: "Tab", Qt.Key_CapsLock: "Caps", Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter", Qt.Key_Space: "Space", Qt.Key_Meta: "Win",
    Qt.Key_F1: "F1", Qt.Key_F2: "F2", Qt.Key_F3: "F3", Qt.Key_F4: "F4",
    Qt.Key_F5: "F5", Qt.Key_F6: "F6", Qt.Key_F7: "F7", Qt.Key_F8: "F8",
    Qt.Key_F9: "F9", Qt.Key_F10: "F10", Qt.Key_F11: "F11", Qt.Key_F12: "F12",
    Qt.Key_Left: "←", Qt.Key_Up: "↑", Qt.Key_Down: "↓", Qt.Key_Right: "→",
}

# Windows scan codes that let us tell the right-hand modifiers apart. Qt
# reports the extended-key flag as bit 8, so RCtrl/RAlt are 0x1D/0x38 | 0x100.
_RIGHT_SHIFT_SCAN = 54
_RIGHT_CTRL_SCAN = 0x11D
_RIGHT_ALT_SCAN = 0x138

_KEY_EVENTS = (QEvent.KeyPress, QEvent.KeyRelease)


class KeyCap(QLabel):
    """A single keyboard key that can be 'lit' once actuated."""

    def __init__(self, text: str, width_units: float):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(40)
        self.setMinimumWidth(int(30 * width_units))
        self._lit = False
        self._apply_style()

    def light(self) -> None:
        if not self._lit:
            self._lit = True
            self._apply_style()

    def reset(self) -> None:
        self._lit = False
        self._apply_style()

    def _apply_style(self) -> None:
        if self._lit:
            self.setStyleSheet(
                f"background:{config.COL_GREEN}; color:{config.COL_BG};"
                f"border:1px solid {config.COL_GREEN}; border-radius:4px;"
                f"font-weight:bold;")
        else:
            self.setStyleSheet(
                f"background:{config.COL_PANEL}; color:{config.COL_TEXT_DIM};"
                f"border:1px solid {config.COL_GRID}; border-radius:4px;")


class KeyboardView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = KeyboardState()
        self._caps: Dict[str, KeyCap] = {}
        self._active = False
        self._result: Dict[str, object] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label("KEYBOARD CONTINUITY TEST", "h2"))
        root.addWidget(label("Swipe your fingers across every row of keys. "
                             "Each key turns green as it registers. Re-press "
                             "any that stay dark. If the F-keys change "
                             "brightness or volume, hold Fn while pressing "
                             "them. The Windows key opens Start — press it "
                             "again to come back.", "dim"))

        head = QHBoxLayout()
        self._ring = CircularProgress(diameter=120)
        self._ring.set_caption("0%", "KEYS")
        head.addWidget(self._ring, 0, Qt.AlignTop)
        self._prompt = label("", "mono")
        self._prompt.setWordWrap(True)
        head.addWidget(self._prompt, 1, Qt.AlignTop)
        root.addLayout(head)

        kb_panel = panel(accent=True)
        kb_l = QVBoxLayout(kb_panel)
        kb_l.setSpacing(5)
        for row in KEYBOARD_ROWS:
            row_l = QHBoxLayout()
            row_l.setSpacing(5)
            for (lbl, width) in row:
                cap = KeyCap(lbl, width)
                self._caps[lbl] = cap
                # Stretch factor keeps proportions as the window resizes.
                row_l.addWidget(cap, int(width * 10))
            row_l.addStretch(1)
            kb_l.addLayout(row_l)
        root.addWidget(kb_panel)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._finish_btn = QPushButton("PROCEED TO TOUCHPAD  ▶")
        self._finish_btn.clicked.connect(self._finish)
        btn_row.addWidget(self._finish_btn)
        root.addLayout(btn_row)
        # Mouse-only, so no key can ever activate them mid-test.
        for btn in (self._skip_btn, self._finish_btn):
            btn.setFocusPolicy(Qt.NoFocus)

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        self._state = KeyboardState()
        self._result = {}
        for cap in self._caps.values():
            cap.reset()
        self._update_prompt()
        self.status.emit("keyboard", "active")
        if not self._active:
            QApplication.instance().installEventFilter(self)
            self._active = True

    def _teardown(self) -> None:
        if self._active:
            QApplication.instance().removeEventFilter(self)
            self._active = False

    # ── global key capture ────────────────────────────────────────────
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._active or event.type() not in _KEY_EVENTS:
            return super().eventFilter(obj, event)
        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            lbl = self._label_for(event)
            if lbl and self._state.press(lbl):
                cap = self._caps.get(lbl)
                if cap:
                    cap.light()
                self._update_prompt()
                if self._state.all_done():
                    self.log.emit("[KEY] All keys registered.")
        return True   # swallow: the test owns the keyboard while it runs

    def _label_for(self, event) -> Optional[str]:
        key = event.key()
        # Right-hand modifiers via Windows scan code where possible.
        if key == Qt.Key_Shift:
            return "RShift" if event.nativeScanCode() == _RIGHT_SHIFT_SCAN \
                else "Shift"
        if key == Qt.Key_Control:
            return "RCtrl" if event.nativeScanCode() == _RIGHT_CTRL_SCAN \
                else "Ctrl"
        if key == Qt.Key_AltGr:
            return "RAlt"
        if key == Qt.Key_Alt:
            return "RAlt" if event.nativeScanCode() == _RIGHT_ALT_SCAN \
                else "Alt"
        if key in _SPECIAL:
            return _SPECIAL[key]
        text = event.text().upper().strip()
        if text and text in self._caps:
            return text
        return None

    def _update_prompt(self) -> None:
        cov = self._state.coverage()
        self._ring.set_value(cov * 100)
        self._ring.set_caption(f"{cov*100:.0f}%", "KEYS")
        self._ring.set_color(config.COL_GREEN if cov >= 1.0 else config.COL_BLUE)
        remaining = sorted(self._state.remaining())
        if remaining:
            shown = ", ".join(remaining[:18]) + (" …" if len(remaining) > 18
                                                 else "")
            self._prompt.setText(
                f"{self._state.pressed_count()}/{self._state.total()} keys "
                f"confirmed.\nStill dark: {shown}")
        else:
            self._prompt.setText("✓ Every key registered. Ready to score.")

    # ── finish ─────────────────────────────────────────────────────────
    def _finish(self) -> None:
        self._teardown()
        summary = self._state.summary()
        self._result = summary
        cov = summary["coverage_pct"]
        self.status.emit("keyboard", light_state(score_keyboard(summary).status))
        self.log.emit(f"[KEY] Coverage {cov}% — "
                      f"{len(summary['dead_or_missed'])} unconfirmed.")
        self.finished.emit(self._result)
        self.next_requested.emit()

    def _skip(self) -> None:
        self._teardown()
        self._result = {"skipped": True}
        self.status.emit("keyboard", "idle")
        self.finished.emit(self._result)
        self.next_requested.emit()
