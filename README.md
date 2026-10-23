# SysCheck — One-Click Laptop Diagnostic Tool

A HUD-styled Windows desktop app that profiles a second-hand laptop's
hardware and walks a non-technical buyer through a few physical checks,
then renders a single **A / B / C / F** health score.

> **Proof-of-concept.** The telemetry code targets real Windows WMI/ACPI
> providers but second-hand machines expose wildly inconsistent data; every
> collector degrades gracefully (showing "Unknown" / "N/A") rather than
> crashing. Run the app **as Administrator** for full S.M.A.R.T. and thermal
> sensor access.

## Features

| Module | What it does |
| ------ | ------------ |
| **Core specs**      | CPU, cores, RAM size/speed, GPU(s)/VRAM, motherboard, BIOS |
| **Storage health**  | S.M.A.R.T. power-on hours, total host writes, failure-predict status |
| **Battery health**  | Design vs full-charge capacity, wear %, cycle count |
| **Battery discharge** | Prompts to unplug AC, then times a live 1% drop and derives the average draw (W) and CPU load behind it |
| **Thermal stress**  | 45 s all-core NumPy load with live temperature graph + throttle detection |
| **Camera clarity**  | Live webcam feed, Laplacian-variance focus score, clean-the-lens re-check |
| **Keyboard test**   | "Piano roll" layout that lights each key as it registers |
| **Touchpad test**   | Cursor-coverage grid plus left/right/double-click and scroll gesture checks |
| **Ports test**      | Live USB plug/unplug detection per port, with USB + display-output inventory |
| **Health score**    | Weighted A/B/C/F verdict with a plain-language per-subsystem summary |

## Project layout

```
SysCheck_POC/
├── main.py                     # entry point
├── requirements.txt
├── build.ps1                   # PyInstaller build script
├── README.md
└── syscheck/
    ├── config.py               # all thresholds + palette
    ├── scoring.py              # telemetry -> A/B/C/F grade
    ├── telemetry/              # MODULE 1 — silent hardware telemetry
    │   ├── wmi_util.py         # per-thread COM/WMI helper
    │   ├── specs.py            # CPU / RAM / GPU / board / BIOS
    │   ├── storage.py          # S.M.A.R.T. parsing
    │   ├── battery.py          # ACPI + powercfg report, + 1%-drop discharge test
    │   ├── thermal.py          # synthetic load + sensor sampling
    │   └── ports.py            # USB enumeration + display-output detection
    ├── diagnostics/            # MODULE 2 — user-guided (Qt-free logic)
    │   ├── camera.py           # Laplacian-variance focus scoring
    │   ├── keyboard.py         # layout data + actuation state machine
    │   └── touchpad.py         # cursor-coverage grid + gesture state machine
    ├── workers/
    │   └── threads.py          # QThread workers (telemetry/battery/thermal/camera/ports)
    └── ui/
        ├── theme.py            # QSS cyber-HUD stylesheet
        ├── widgets.py          # progress ring, console, indicator, graph
        ├── main_window.py      # console rail + stacked diagnostic flow
        └── views/              # splash, telemetry, battery, thermal, camera,
                                #   keyboard, touchpad, ports, results
```

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Requires **Python 3.11+** on Windows 10/11.

## Build a standalone .exe

```powershell
pip install pyinstaller
.\build.ps1
```

Or the exact one-liner (no console window):

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name SysCheck ^
  --collect-submodules wmi --collect-submodules cpuinfo ^
  --collect-data cv2 --hidden-import win32timezone main.py
```

The result is `dist\SysCheck.exe`.

## Notes & limitations

- **Administrator rights**: S.M.A.R.T. attributes (`MSStorageDriver_*`) and
  ACPI thermal zones (`MSAcpi_ThermalZoneTemperature`) frequently require
  elevation. Without it the app still runs and marks those fields N/A.
- **GPU VRAM**: WMI's `AdapterRAM` is a signed 32-bit field (unreliable above
  4 GB); NVIDIA cards are enriched via `GPUtil` when present.
- **Thermal sensors**: many consumer laptops don't expose a usable thermal
  zone to user space; the test then falls back to clock-based throttle
  detection and reports the sensor as unavailable.
- **Keyboard right-hand modifiers**: left/right Shift are told apart by scan
  code; right Ctrl/Alt may remain "unconfirmed" on some layouts.
- **Battery discharge**: the laptop must run off the battery, so the step waits
  for the charger to be removed before timing a 1% drop. Windows reports charge
  as whole percentages, so if a full 1% hasn't dropped within the timeout the
  tool falls back to the OS's own `secsleft` estimate (flagged as *estimated*).
  Reconnecting AC mid-test voids the reading.
- **Ports test**: an empty port can't be verified by software, so the check is
  driven by live USB arrival/removal events — plug a device into each port in
  turn. HDMI/DisplayPort outputs are listed for reference but not actively
  exercised.
