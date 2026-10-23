"""Hardware telemetry subsystem (Module 1).

Each submodule exposes a single collector function/class that returns a
plain dict of results plus a human-readable status, so the UI layer never
has to know how the data was obtained.

    specs    -> CPU / RAM / GPU / board / BIOS
    storage  -> S.M.A.R.T. (power-on hours, host writes, health)
    battery  -> design vs full-charge capacity, wear, cycle count,
                plus a live 1%-drop discharge test
    thermal  -> synthetic CPU load + temperature monitoring
    ports    -> live USB arrival/removal + display-output enumeration
"""

from .specs import collect_core_specs
from .storage import collect_storage_health
from .battery import collect_battery_health, run_discharge_test
from .ports import list_usb_devices, list_display_outputs

__all__ = [
    "collect_core_specs",
    "collect_storage_health",
    "collect_battery_health",
    "run_discharge_test",
    "list_usb_devices",
    "list_display_outputs",
]
