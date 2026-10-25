"""Background workers (QThread) that keep heavy I/O and compute off the GUI
thread so the HUD never freezes mid-scan."""

from .threads import (TelemetryWorker, ThermalWorker, CameraWorker,
                      BatteryDischargeWorker, PortsWorker, PerformanceWorker)

__all__ = ["TelemetryWorker", "ThermalWorker", "CameraWorker",
           "BatteryDischargeWorker", "PortsWorker", "PerformanceWorker"]
