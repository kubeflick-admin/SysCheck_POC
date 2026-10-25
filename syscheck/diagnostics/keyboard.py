"""Keyboard continuity ("piano roll") model.

``KEYBOARD_ROWS`` describes a compact laptop layout as rows of
``(label, width_units)`` tuples where ``width_units`` scales the key's
rendered width (1.0 = a normal key). ``KeyboardState`` is the Qt-free
state machine: the view reports each actuated label, and the state tracks
coverage so we can prompt the user to re-press whatever stayed dark.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

Key = Tuple[str, float]

# A pragmatic laptop layout. Labels must be unique (they are the keys the
# view lights up). Modifier/space keys are included so a full finger-swipe
# can register them. Fn is left out: the keyboard firmware handles it and
# it never reaches the OS, so it could never light up.
KEYBOARD_ROWS: List[List[Key]] = [
    [("Esc", 1.0), ("F1", 1.0), ("F2", 1.0), ("F3", 1.0), ("F4", 1.0),
     ("F5", 1.0), ("F6", 1.0), ("F7", 1.0), ("F8", 1.0), ("F9", 1.0),
     ("F10", 1.0), ("F11", 1.0), ("F12", 1.0), ("Del", 1.0)],
    [("`", 1.0), ("1", 1.0), ("2", 1.0), ("3", 1.0), ("4", 1.0), ("5", 1.0),
     ("6", 1.0), ("7", 1.0), ("8", 1.0), ("9", 1.0), ("0", 1.0), ("-", 1.0),
     ("=", 1.0), ("Back", 2.0)],
    [("Tab", 1.5), ("Q", 1.0), ("W", 1.0), ("E", 1.0), ("R", 1.0), ("T", 1.0),
     ("Y", 1.0), ("U", 1.0), ("I", 1.0), ("O", 1.0), ("P", 1.0), ("[", 1.0),
     ("]", 1.0), ("\\", 1.5)],
    [("Caps", 1.75), ("A", 1.0), ("S", 1.0), ("D", 1.0), ("F", 1.0),
     ("G", 1.0), ("H", 1.0), ("J", 1.0), ("K", 1.0), ("L", 1.0), (";", 1.0),
     ("'", 1.0), ("Enter", 2.25)],
    [("Shift", 2.25), ("Z", 1.0), ("X", 1.0), ("C", 1.0), ("V", 1.0),
     ("B", 1.0), ("N", 1.0), ("M", 1.0), (",", 1.0), (".", 1.0), ("/", 1.0),
     ("RShift", 2.75)],
    [("Ctrl", 1.5), ("Win", 1.25), ("Alt", 1.25), ("Space", 5.0),
     ("RAlt", 1.25), ("RCtrl", 1.25), ("←", 1.0), ("↑", 1.0), ("↓", 1.0),
     ("→", 1.0)],
]


def all_labels() -> List[str]:
    return [label for row in KEYBOARD_ROWS for (label, _w) in row]


class KeyboardState:
    """Tracks which keys have been actuated during the continuity test."""

    def __init__(self) -> None:
        self._all: Set[str] = set(all_labels())
        self._pressed: Set[str] = set()

    def press(self, label: str) -> bool:
        """Mark ``label`` actuated. Returns True if it is a known, new key."""
        if label in self._all and label not in self._pressed:
            self._pressed.add(label)
            return True
        return False

    def is_pressed(self, label: str) -> bool:
        return label in self._pressed

    def remaining(self) -> Set[str]:
        """Keys not yet actuated — candidates for re-press / dead keys."""
        return self._all - self._pressed

    def pressed_count(self) -> int:
        return len(self._pressed)

    def total(self) -> int:
        return len(self._all)

    def coverage(self) -> float:
        """Fraction 0.0–1.0 of keys actuated."""
        return len(self._pressed) / len(self._all) if self._all else 1.0

    def all_done(self) -> bool:
        return not self.remaining()

    def summary(self) -> Dict[str, object]:
        return {
            "pressed": self.pressed_count(),
            "total": self.total(),
            "coverage_pct": round(self.coverage() * 100, 1),
            "dead_or_missed": sorted(self.remaining()),
        }
