"""Camera clarity view — live webcam feed with a Laplacian-variance focus
score, the clean-the-lens prompt, and a before/after improvement check."""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QWidget,
                               QLabel)

from ... import config
from ...workers import CameraWorker
from ...diagnostics.camera import classify_sharpness, evaluate_delta
from ...scoring import score_camera
from ..widgets import CircularProgress
from ._common import label, light_state, panel


class CameraView(QWidget):
    log = Signal(str)
    status = Signal(str, str)
    finished = Signal(dict)
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[CameraWorker] = None
        self._smoothed = 0.0
        self._baseline: Optional[float] = None
        self._phase = "measure"   # measure -> recheck
        self._result: Dict[str, object] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label("CAMERA CLARITY TEST", "h2"))

        body = QHBoxLayout()
        # Live feed
        feed_panel = panel(accent=True)
        fp = QVBoxLayout(feed_panel)
        self._video = QLabel("initialising capture device …")
        self._video.setAlignment(Qt.AlignCenter)
        self._video.setMinimumSize(480, 360)
        self._video.setStyleSheet(f"color:{config.COL_TEXT_DIM};"
                                  f"background:#070808;")
        fp.addWidget(self._video, 1)
        body.addWidget(feed_panel, 1)

        # Side panel: focus ring + prompt
        side = QVBoxLayout()
        self._ring = CircularProgress(diameter=150)
        self._ring.set_caption("—", "FOCUS")
        side.addWidget(self._ring, 0, Qt.AlignHCenter)
        self._prompt = label("Point the webcam at a detailed, well-lit "
                             "surface, then capture a focus reading.", "dim")
        self._prompt.setWordWrap(True)
        self._prompt.setMinimumWidth(240)
        side.addWidget(self._prompt)
        side.addStretch(1)
        body.addLayout(side)
        root.addLayout(body, 1)

        btn_row = QHBoxLayout()
        self._skip_btn = QPushButton("SKIP")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)
        self._action_btn = QPushButton("CAPTURE FOCUS")
        self._action_btn.clicked.connect(self._on_action)
        btn_row.addWidget(self._action_btn)
        self._next_btn = QPushButton("PROCEED TO KEYBOARD  ▶")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._continue)
        btn_row.addWidget(self._next_btn)
        root.addLayout(btn_row)

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        self._smoothed = 0.0
        self._baseline = None
        self._phase = "measure"
        self._result = {}
        self._next_btn.setEnabled(False)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("CAPTURE FOCUS")
        self.status.emit("camera", "active")

        self._worker = CameraWorker(config.CAMERA_INDEX)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.score.connect(self._on_score)
        self._worker.log.connect(self.log.emit)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def stop_camera(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(1500)

    # ── slots ─────────────────────────────────────────────────────────
    def _on_frame(self, img: QImage) -> None:
        pix = QPixmap.fromImage(img).scaled(
            self._video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._video.setPixmap(pix)

    def _on_score(self, var: float) -> None:
        # Exponential moving average smooths frame-to-frame jitter.
        self._smoothed = var if self._smoothed == 0 else \
            0.8 * self._smoothed + 0.2 * var
        # Map score onto the ring: threshold == full green.
        pct = min(100.0, self._smoothed / config.CAMERA_BLUR_THRESHOLD * 100)
        self._ring.set_value(pct)
        self._ring.set_caption(f"{self._smoothed:.0f}", "FOCUS")
        self._ring.set_color(config.COL_GREEN
                             if self._smoothed >= config.CAMERA_BLUR_THRESHOLD
                             else config.COL_AMBER)

    def _on_error(self, msg: str) -> None:
        self._video.setText(f"⚠  {msg}")
        self._action_btn.setEnabled(False)
        if self._result:
            return   # a reading was already captured; keep it
        # A webcam that won't open is unverified, not a skipped test.
        self._result = {"unavailable": True, "reason": msg}
        self._prompt.setText("Webcam unavailable — it will be reported as "
                             "not verified. Proceed when ready.")
        self._next_btn.setEnabled(True)
        self.status.emit("camera", light_state(score_camera(self._result).status))

    def _on_action(self) -> None:
        score = self._smoothed
        if self._phase == "measure":
            self._baseline = score
            kind = classify_sharpness(score)
            self.log.emit(f"[CAM] Baseline focus score = {score:.1f} ({kind}).")
            if kind == "sharp":
                self._prompt.setText("✓ Image is sharp. Webcam looks good.")
                self._finalise(score, blurry=False, improved=False)
            else:
                self._prompt.setText(
                    "Lens appears blurry or smudged. Please wipe the camera "
                    "lens with a clean cloth, then press RE-CHECK.")
                self.status.emit("camera", "warn")
                self._action_btn.setText("RE-CHECK")
                self._phase = "recheck"
        else:  # recheck
            improved, delta = evaluate_delta(self._baseline or 0.0, score)
            kind = classify_sharpness(score)
            self.log.emit(f"[CAM] Re-check score = {score:.1f} "
                          f"(Δ {delta:+.1f}, {kind}).")
            if improved:
                self._prompt.setText(
                    f"✓ Clarity improved by {delta:.0f}. "
                    + ("Image is now sharp." if kind == "sharp"
                       else "Still a little soft, but better."))
            else:
                self._prompt.setText(
                    "No real improvement after cleaning — the lens or sensor "
                    "may be physically degraded.")
            self._finalise(score, blurry=(kind == "blurry"), improved=improved)

    def _finalise(self, score: float, blurry: bool, improved: bool) -> None:
        self._result = {"final_score": round(score, 1),
                        "blurry": blurry, "improved": improved}
        self._action_btn.setEnabled(False)
        self._next_btn.setEnabled(True)
        self.status.emit("camera", light_state(score_camera(self._result).status))

    def _skip(self) -> None:
        if not self._result.get("unavailable"):
            self._result = {"skipped": True, "reason": "Camera test skipped."}
            self.status.emit("camera", "idle")
        self._continue()

    def _continue(self) -> None:
        self.stop_camera()
        if not self._result:
            self._result = {"skipped": True, "reason": "No reading captured."}
        self.finished.emit(self._result)
        self.next_requested.emit()
