"""QThread workers.

Every worker is a ``QThread`` subclass that does its work in ``run()`` and
communicates back exclusively through Qt signals (thread-safe queued
connections). The GUI thread never blocks and never touches worker-owned
objects directly.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .. import config
from ..telemetry import (collect_core_specs, collect_storage_health,
                         collect_battery_health, run_discharge_test,
                         list_usb_devices, list_display_outputs)
from ..telemetry.thermal import run_thermal_stress
from ..diagnostics.camera import laplacian_variance


# ─────────────────────────────────────────────────────────────────────
class TelemetryWorker(QThread):
    """Silently gathers specs, storage, and battery telemetry."""

    log = Signal(str)
    progress = Signal(int)                 # 0–100 across the three stages
    component_done = Signal(str, dict)     # (name, data)
    finished_all = Signal(dict)            # full results dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self) -> None:
        """Stop after the current stage (a WMI call can't be interrupted)."""
        self._cancel = True

    def run(self) -> None:  # noqa: D401
        results: dict = {}
        stages = [
            ("specs", "CORE SPECS", collect_core_specs),
            ("storage", "STORAGE S.M.A.R.T.", collect_storage_health),
            ("battery", "BATTERY ACPI", collect_battery_health),
        ]
        for i, (key, title, fn) in enumerate(stages):
            if self._cancel:
                return
            self.log.emit(f"==== MODULE :: {title} ====")
            try:
                data = fn(self.log.emit)
            except Exception as exc:  # noqa: BLE001
                self.log.emit(f"[ERROR] {title} collector crashed: {exc}")
                data = {}
            results[key] = data
            self.component_done.emit(key, data)
            self.progress.emit(int((i + 1) / len(stages) * 100))
        self.log.emit("==== TELEMETRY SWEEP COMPLETE ====")
        self.finished_all.emit(results)


# ─────────────────────────────────────────────────────────────────────
class ThermalWorker(QThread):
    """Runs the synthetic CPU load and streams sensor samples."""

    log = Signal(str)
    # elapsed_s, temp_c (or None), freq_mhz (or None), cpu_pct
    sample = Signal(float, object, object, float)
    finished_thermal = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401
        self.log.emit("==== MODULE :: THERMAL STRESS ====")

        def on_sample(elapsed, temp, freq, cpu):
            self.sample.emit(elapsed, temp, freq, cpu)

        result = run_thermal_stress(
            log=self.log.emit,
            on_sample=on_sample,
            should_stop=lambda: self._cancel,
        )
        self.finished_thermal.emit(result)


# ─────────────────────────────────────────────────────────────────────
class BatteryDischargeWorker(QThread):
    """Waits for AC to be unplugged, then times a 1% battery drop."""

    log = Signal(str)
    # phase ("wait"|"drain"), percent (or None), cpu_pct, elapsed_s
    sample = Signal(str, object, float, float)
    finished_discharge = Signal(dict)

    def __init__(self, full_mwh: Optional[int] = None, parent=None):
        super().__init__(parent)
        self._full_mwh = full_mwh
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401
        self.log.emit("==== MODULE :: BATTERY DISCHARGE ====")
        result = run_discharge_test(
            full_mwh=self._full_mwh,
            log=self.log.emit,
            on_sample=lambda ph, pct, cpu, el: self.sample.emit(ph, pct, cpu, el),
            should_stop=lambda: self._cancel,
        )
        self.finished_discharge.emit(result)


# ─────────────────────────────────────────────────────────────────────
class PortsWorker(QThread):
    """Polls the attached-USB set and streams arrival / removal events."""

    log = Signal(str)
    inventory = Signal(dict, list)        # {usb id: name}, [display outputs]
    event = Signal(str, str)              # ("added"|"removed"), device name
    counts = Signal(int, int)             # total arrivals, total removals

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._added = 0
        self._removed = 0

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:  # noqa: D401
        self.log.emit("==== MODULE :: PORTS / PERIPHERALS ====")
        baseline = list_usb_devices()
        displays = list_display_outputs(self.log.emit)
        self.log.emit(f"[PORT] {len(baseline)} USB device(s) attached at start; "
                      f"{len(displays)} display output(s) detected.")
        self.inventory.emit(baseline, displays)

        known = dict(baseline)
        self._running = True
        interval = 1.0 / max(1, config.PORTS_POLL_HZ)
        while self._running:
            time.sleep(interval)
            if not self._running:
                break
            current = list_usb_devices()
            for dev_id, name in current.items():
                if dev_id not in known:
                    self._added += 1
                    self.log.emit(f"[PORT] + arrival: {name}")
                    self.event.emit("added", name)
            for dev_id, name in list(known.items()):
                if dev_id not in current:
                    self._removed += 1
                    self.log.emit(f"[PORT] - removed: {name}")
                    self.event.emit("removed", name)
            if current != known:
                known = current
                self.counts.emit(self._added, self._removed)
        self.log.emit("[PORT] Port monitor stopped.")


# ─────────────────────────────────────────────────────────────────────
class CameraWorker(QThread):
    """Streams webcam frames and a rolling Laplacian-variance focus score.

    cv2 is imported lazily inside ``run`` so a machine without a webcam (or
    without the DLLs) fails softly with an ``error`` signal rather than at
    import time.
    """

    frame_ready = Signal(QImage)
    score = Signal(float)
    log = Signal(str)
    error = Signal(str)

    def __init__(self, camera_index: int = 0, parent=None):
        super().__init__(parent)
        self._index = camera_index
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:  # noqa: D401
        import cv2  # local import: keeps GUI start-up resilient

        self.log.emit(f"[CAM] Opening capture device {self._index} ...")
        cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not cap or not cap.isOpened():
            self.error.emit("Could not open the webcam (device busy or absent).")
            self.log.emit("[CAM] ERROR: VideoCapture failed to open.")
            return

        self._running = True
        self.log.emit("[CAM] Live feed established. Computing focus score ...")
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.error.emit("Lost the webcam feed.")
                    break

                var = laplacian_variance(frame)
                self.score.emit(var)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
                self.frame_ready.emit(img)

                # ~30 fps ceiling; keeps CPU reasonable.
                time.sleep(1 / 30)
        finally:
            cap.release()
            self.log.emit("[CAM] Capture device released.")
