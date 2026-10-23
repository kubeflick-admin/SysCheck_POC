"""Camera clarity scoring via Laplacian variance.

The variance of the Laplacian is the classic no-reference focus metric: a
sharp frame has strong high-frequency edges (high variance), a blurry or
smudged frame has few (low variance). We compare the score against
``config.CAMERA_BLUR_THRESHOLD`` to decide whether to prompt the user to
clean the lens, and track the delta after they wipe it.
"""

from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np

from .. import config


def laplacian_variance(frame: np.ndarray) -> float:
    """Return the focus score (variance of the Laplacian) for a BGR frame."""
    if frame is None or frame.size == 0:
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def classify_sharpness(score: float) -> str:
    """Map a focus score to ``"sharp"`` or ``"blurry"``."""
    return "sharp" if score >= config.CAMERA_BLUR_THRESHOLD else "blurry"


def evaluate_delta(before: float, after: float) -> Tuple[bool, float]:
    """Compare pre/post-wipe scores.

    Returns ``(improved, delta)`` where ``improved`` is True when the gain
    clears ``config.CAMERA_IMPROVE_DELTA``.
    """
    delta = after - before
    return (delta >= config.CAMERA_IMPROVE_DELTA, delta)
