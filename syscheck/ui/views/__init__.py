"""Step-by-step diagnostic views, each a self-contained QWidget that owns
its worker (where applicable) and emits navigation signals."""

from .splash_view import SplashView
from .telemetry_view import TelemetryView
from .battery_view import BatteryView
from .thermal_view import ThermalView
from .camera_view import CameraView
from .keyboard_view import KeyboardView
from .touchpad_view import TouchpadView
from .ports_view import PortsView
from .results_view import ResultsView

__all__ = [
    "SplashView",
    "TelemetryView",
    "BatteryView",
    "ThermalView",
    "CameraView",
    "KeyboardView",
    "TouchpadView",
    "PortsView",
    "ResultsView",
]
