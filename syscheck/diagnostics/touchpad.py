"""Touchpad continuity model.

Qt-free state for the touchpad test. The view reports cursor positions as
normalised ``(x, y)`` in ``[0, 1]`` over the pad area plus the gestures it
observes (clicks, scroll); :class:`TouchpadState` turns cursor travel into
grid-cell *coverage* so we can tell a pad with a dead patch (cursor can't
reach a corner) from a healthy one, and tracks which gestures still need
demonstrating.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

# Gestures the operator must demonstrate for a complete pass.
GESTURES: List[Tuple[str, str]] = [
    ("left_click", "Left click"),
    ("right_click", "Right click"),
    ("double_click", "Double click"),
    ("scroll", "Two-finger scroll"),
]


class TouchpadState:
    """Tracks grid coverage and gesture completion during the pad test."""

    def __init__(self, cols: int, rows: int) -> None:
        self._cols = max(1, cols)
        self._rows = max(1, rows)
        self._visited: Set[Tuple[int, int]] = set()
        self._gestures: Dict[str, bool] = {g: False for g, _ in GESTURES}

    # ── coverage ───────────────────────────────────────────────────────
    def visit(self, nx: float, ny: float) -> Tuple[int, int] | None:
        """Record a cursor position (normalised 0–1). Returns the newly lit
        cell ``(col, row)`` or ``None`` if that cell was already covered."""
        col = min(self._cols - 1, max(0, int(nx * self._cols)))
        row = min(self._rows - 1, max(0, int(ny * self._rows)))
        cell = (col, row)
        if cell in self._visited:
            return None
        self._visited.add(cell)
        return cell

    def is_visited(self, col: int, row: int) -> bool:
        return (col, row) in self._visited

    def total_cells(self) -> int:
        return self._cols * self._rows

    def covered_cells(self) -> int:
        return len(self._visited)

    def coverage(self) -> float:
        return self.covered_cells() / self.total_cells()

    # ── gestures ───────────────────────────────────────────────────────
    def mark(self, gesture: str) -> bool:
        """Mark a gesture demonstrated. Returns True if it is newly set."""
        if gesture in self._gestures and not self._gestures[gesture]:
            self._gestures[gesture] = True
            return True
        return False

    def gesture_done(self, gesture: str) -> bool:
        return self._gestures.get(gesture, False)

    def pending_gestures(self) -> List[str]:
        return [g for g, done in self._gestures.items() if not done]

    def all_done(self) -> bool:
        return self.coverage() >= 1.0 and not self.pending_gestures()

    # ── summary ────────────────────────────────────────────────────────
    def summary(self) -> Dict[str, object]:
        return {
            "coverage_pct": round(self.coverage() * 100, 1),
            "cells": f"{self.covered_cells()}/{self.total_cells()}",
            "gestures": dict(self._gestures),
            "missing_gestures": self.pending_gestures(),
        }
