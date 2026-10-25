"""Reusable HUD widgets: progress ring, telemetry console, indicator light,
a live line graph and a drive health bar. All are custom-painted to get
the neon look that a stock Qt widget can't provide.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF
from PySide6.QtWidgets import QWidget, QPlainTextEdit

from .. import config


# ─────────────────────────────────────────────────────────────────────
class CircularProgress(QWidget):
    """An animated circular progress ring with a centred caption."""

    def __init__(self, diameter: int = 160, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._value = 0.0            # 0–100
        self._caption = ""
        self._subcaption = ""
        self._color = QColor(config.COL_BLUE)
        self.setFixedSize(diameter, diameter)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    def set_caption(self, caption: str, sub: str = "") -> None:
        self._caption, self._subcaption = caption, sub
        self.update()

    def set_color(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        pad = 12
        rect = QRectF(pad, pad, side - 2 * pad, side - 2 * pad)

        # Track
        p.setPen(QPen(QColor(config.COL_GRID), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 0, 360 * 16)

        # Value arc (start at top, clockwise)
        p.setPen(QPen(self._color, 10, Qt.SolidLine, Qt.RoundCap))
        span = int(-self._value / 100.0 * 360 * 16)
        p.drawArc(rect, 90 * 16, span)

        # Centre caption
        p.setPen(QColor(config.COL_TEXT))
        f = QFont(config.MONO_FONT)
        f.setPointSize(18)
        f.setBold(True)
        p.setFont(f)
        cap = self._caption or f"{self._value:.0f}%"
        p.drawText(rect, Qt.AlignCenter, cap)

        if self._subcaption:
            p.setPen(QColor(config.COL_TEXT_DIM))
            f2 = QFont(config.MONO_FONT)
            f2.setPointSize(9)
            p.setFont(f2)
            sub_rect = QRectF(rect.left(), rect.center().y() + 16,
                              rect.width(), 22)
            p.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignTop, self._subcaption)
        p.end()


# ─────────────────────────────────────────────────────────────────────
class TelemetryConsole(QPlainTextEdit):
    """Auto-scrolling read-only log pane with a capped backlog."""

    def __init__(self, max_lines: int = 500, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("console")
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.appendPlainText(f"[{ts}] {line}")
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ─────────────────────────────────────────────────────────────────────
class IndicatorLight(QWidget):
    """A pulsing status dot with a text label.

    States: ``idle`` (dim), ``active`` (blue, pulsing), ``pass`` (green),
    ``warn`` (amber), ``fail`` (red).
    """

    _COLORS = {
        "idle": config.COL_TEXT_DIM,
        "active": config.COL_BLUE,
        "pass": config.COL_GREEN,
        "warn": config.COL_AMBER,
        "fail": config.COL_RED,
    }

    def __init__(self, label: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._label = label
        self._state = "idle"
        self._phase = 0.0
        self.setMinimumHeight(24)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)

    def set_state(self, state: str) -> None:
        if state in self._COLORS:
            self._state = state
            self.update()

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def _tick(self) -> None:
        # Only pulse in the "active" state to save paints.
        if self._state == "active":
            self._phase = (self._phase + 0.12) % (2 * 3.14159)
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base = QColor(self._COLORS[self._state])

        # Pulse the glow radius when active.
        glow = 1.0
        if self._state == "active":
            glow = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(self._phase))

        cy = self.height() / 2
        r = 6
        # Outer glow
        glow_col = QColor(base)
        glow_col.setAlphaF(0.35 * glow)
        p.setBrush(QBrush(glow_col))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, cy), r + 5 * glow, r + 5 * glow)
        # Core dot
        p.setBrush(QBrush(base))
        p.drawEllipse(QPointF(12, cy), r, r)

        # Label
        p.setPen(QColor(config.COL_TEXT))
        f = QFont(config.MONO_FONT)
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(QRectF(28, 0, self.width() - 28, self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self._label)
        p.end()


# ─────────────────────────────────────────────────────────────────────
class LineGraph(QWidget):
    """A live line graph for the thermal stress test.

    Feed values with :meth:`push`. Optionally draw a horizontal threshold
    line (e.g. the 90 °C critical temperature).
    """

    def __init__(self, max_points: int = 120, y_max: float = 100.0,
                 threshold: Optional[float] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._data: Deque[float] = deque(maxlen=max_points)
        self._y_max = y_max
        self._y_min = 0.0
        self._threshold = threshold
        self.setMinimumHeight(160)

    def clear(self) -> None:
        self._data.clear()
        self.update()

    def push(self, value: float) -> None:
        self._data.append(float(value))
        # Auto-expand the ceiling so spikes stay on-screen.
        if value > self._y_max:
            self._y_max = value * 1.1
        self.update()

    def _y_to_px(self, value: float, h: int, pad: int) -> float:
        span = max(1e-6, self._y_max - self._y_min)
        frac = (value - self._y_min) / span
        return h - pad - frac * (h - 2 * pad)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, pad = self.width(), self.height(), 10

        # Background + border
        p.fillRect(self.rect(), QColor("#070808"))
        p.setPen(QPen(QColor(config.COL_GRID), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Horizontal grid lines
        for i in range(1, 4):
            y = pad + i * (h - 2 * pad) / 4
            p.setPen(QPen(QColor(config.COL_GRID), 1, Qt.DotLine))
            p.drawLine(pad, int(y), w - pad, int(y))

        # Threshold line
        if self._threshold is not None and self._threshold <= self._y_max:
            ty = self._y_to_px(self._threshold, h, pad)
            p.setPen(QPen(QColor(config.COL_RED), 1, Qt.DashLine))
            p.drawLine(pad, int(ty), w - pad, int(ty))
            p.setPen(QColor(config.COL_RED))
            f = QFont(config.MONO_FONT)
            f.setPointSize(8)
            p.setFont(f)
            p.drawText(w - pad - 70, int(ty) - 4, f"{self._threshold:.0f} CRIT")

        # Data polyline
        n = len(self._data)
        if n >= 2:
            step = (w - 2 * pad) / (n - 1)
            poly = QPolygonF()
            for i, v in enumerate(self._data):
                poly.append(QPointF(pad + i * step, self._y_to_px(v, h, pad)))
            p.setPen(QPen(QColor(config.COL_BLUE), 2))
            p.drawPolyline(poly)

            # Current value readout
            last = self._data[-1]
            p.setPen(QColor(config.COL_GREEN))
            f = QFont(config.MONO_FONT)
            f.setPointSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(pad + 2, pad + 14, f"{last:.1f}")
        else:
            p.setPen(QColor(config.COL_TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "awaiting sensor data …")
        p.end()


# ─────────────────────────────────────────────────────────────────────
class HealthBar(QWidget):
    """A wide segmented bar for a 0–100 drive Health %, with the figure
    printed at its right end. ``None`` draws an empty bar and "N/A"."""

    SEGMENTS = 25

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._value: Optional[float] = None
        self._color = QColor(config.COL_TEXT_DIM)
        self.setMinimumSize(240, 44)

    def value(self) -> Optional[float]:
        return self._value

    def set_value(self, value: Optional[float], hex_color: str) -> None:
        self._value = (None if value is None
                       else max(0.0, min(100.0, float(value))))
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, pad, gap, text_w = self.width(), self.height(), 6, 3, 100

        p.fillRect(self.rect(), QColor("#070808"))
        p.setPen(QPen(QColor(config.COL_GRID), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Segments: lit up to the value, the rest a faint ghost of the colour.
        lit = 0
        if self._value is not None:
            lit = round(self._value / 100 * self.SEGMENTS)
            lit = max(lit, 1) if self._value > 0 else 0
        ghost = QColor(self._color if self._value is not None
                       else config.COL_GRID)
        ghost.setAlphaF(0.15 if self._value is not None else 0.6)
        seg_w = (w - text_w - 2 * pad - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        for i in range(self.SEGMENTS):
            x = pad + i * (seg_w + gap)
            p.fillRect(QRectF(x, pad, seg_w, h - 2 * pad),
                       self._color if i < lit else ghost)

        f = QFont(config.MONO_FONT)
        f.setPointSize(18)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self._color if self._value is not None
                 else QColor(config.COL_TEXT_DIM))
        text = "N/A" if self._value is None else f"{self._value:.0f}%"
        p.drawText(QRectF(w - text_w, 0, text_w - pad - 4, h),
                   Qt.AlignVCenter | Qt.AlignRight, text)
        p.end()
