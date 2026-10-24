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
| **Storage health**  | A 0–100 **Drive Health %** per drive from its S.M.A.R.T. damage counters, SSD wear and the drive's own verdict, plus an estimated remaining life, in plain English |
| **Storage page**    | Per drive: Health % bar, summary, remaining life, model / serial / firmware / interface / size, temperatures, power-on time, data written / read and the full S.M.A.R.T. table |
| **Battery health**  | Design vs full-charge capacity, wear %, cycle count |
| **Battery discharge** | Prompts to unplug AC, then measures the average draw (W), time per 1% and the CPU load behind it |
| **Thermal stress**  | 45 s all-core NumPy load with live temperature graph + throttle detection (sustained clock vs base clock) |
| **Camera clarity**  | Live webcam feed, Laplacian-variance focus score, clean-the-lens re-check |
| **Keyboard test**   | "Piano roll" layout that lights each key as it registers |
| **Touchpad test**   | Cursor-coverage grid plus left/right/double-click and scroll gesture checks |
| **Ports test**      | Live USB plug/unplug detection per port, with USB + display-output inventory |
| **Health score**    | Weighted A/B/C/F verdict with a plain-language per-subsystem summary |
| **Export report**   | One self-contained HTML file (opens in any browser, prints to PDF) with the grade, every result and the full storage section; serial numbers can be hidden |

Checks that ran but couldn't verify a part (no USB device plugged in, a
sensor that can't be read, a webcam that won't open) are reported as
**Not verified**. They are left out of the grade instead of failing it.

## Project layout

```
SysCheck_POC/
├── main.py                     # entry point
├── requirements.txt
├── requirements-dev.txt        # test / lint dependencies
├── pyproject.toml              # pytest + ruff settings
├── build.ps1                   # PyInstaller build script
├── README.md
├── tests/                      # headless pytest suite (fake hardware)
├── vendor/smartmontools/       # bundled smartctl.exe + licence + source note
└── syscheck/
    ├── config.py               # all thresholds + palette
    ├── scoring.py              # telemetry -> A/B/C/F grade
    ├── export.py               # single-file HTML report
    ├── diskhealth/             # Qt- and WMI-free drive judgement
    │   ├── health.py           # Health % + estimated remaining life
    │   └── describe.py         # plain-English wording, S.M.A.R.T. table rows
    ├── telemetry/              # MODULE 1 — silent hardware telemetry
    │   ├── wmi_util.py         # per-thread COM/WMI helper
    │   ├── specs.py            # CPU / RAM / GPU / board / BIOS
    │   ├── storage.py          # drive list + layered S.M.A.R.T. collection
    │   ├── smart_sources/      # one module per S.M.A.R.T. source
    │   │   ├── schema.py       # normalised per-drive record
    │   │   ├── smartctl.py     # A: bundled smartctl, JSON output
    │   │   ├── reliability.py  # B: MSFT_StorageReliabilityCounter
    │   │   ├── wmi_smart.py    # C: MSStorageDriver_FailurePredict* blobs
    │   │   └── matching.py     # same drive across sources
    │   ├── battery.py          # ACPI + powercfg report, + discharge test
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
        └── views/              # splash, telemetry, storage, battery, thermal,
                                #   camera, keyboard, touchpad, ports, results
```

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Requires **Python 3.11 or 3.12** on Windows 10/11. The pinned dependencies
have no wheels for 3.13+ yet. Use the python.org installer, not the
Microsoft Store placeholder.

You don't need to rebuild the .exe to try a change: run `python main.py`
again. From a WSL checkout, a Windows venv can run it in place:

```powershell
C:\dev\syscheck-venv\Scripts\python \\wsl.localhost\Ubuntu\home\<user>\SysCheck_POC\main.py
```

## Tests

The scoring, diagnostics, discharge and thermal logic, and the views, are
tested headlessly. Hardware readings are faked, so the suite runs on
Linux/WSL or Windows with no laptop-specific setup:

```bash
python -m venv .venv-test
.venv-test/bin/pip install -r requirements-dev.txt
.venv-test/bin/pytest
.venv-test/bin/ruff check .
```

Real-hardware behaviour (WMI, sensors, webcam, USB) still needs a manual
run on Windows, preferably elevated.

## Build a standalone .exe

```powershell
pip install pyinstaller
.\build.ps1
```

Or the exact one-liner (no console window):

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name SysCheck ^
  --collect-submodules wmi --collect-submodules cpuinfo ^
  --collect-data cv2 --hidden-import win32timezone ^
  --add-binary "vendor\smartmontools\smartctl.exe;vendor\smartmontools" ^
  --add-data "vendor\smartmontools\COPYING.txt;vendor\smartmontools" ^
  --add-data "vendor\smartmontools\SOURCE.txt;vendor\smartmontools" ^
  --upx-exclude smartctl.exe main.py
```

The result is `dist\SysCheck.exe`.

## Notes & limitations

- **Administrator rights**: ACPI thermal zones
  (`MSAcpi_ThermalZoneTemperature`) and most drive health data need
  elevation. Without it the app still runs and marks those fields N/A.
- **Drive health sources**: each drive is read by the first of three layers
  that works: the bundled `smartctl` (JSON output), then Windows'
  `MSFT_StorageReliabilityCounter`, then the raw ATA S.M.A.R.T. blobs
  (`MSStorageDriver_*`). NVMe drives are usually readable by `smartctl`
  without elevation; SATA drives and both Windows layers need
  Administrator. Readings a layer can't provide show as N/A rather than 0,
  and data written is only reported when its unit is known.
- **Drive Health %**: starts at 100% and each damage counter (reallocated,
  pending and uncorrectable sectors, NVMe media errors, ...) takes a capped
  share away; the shares multiply, so different problems add up faster.
  A drive that reports itself failing, a value at its failure threshold, a
  low NVMe spare and SSD wear cap it further, and the manufacturer's own
  value-vs-threshold figure wins when lower. Power-on hours never lower
  health; they shorten the remaining-life estimate, which is always worded
  as an estimate and capped at "more than 1000 days". All weights are in
  `config.py`. Without elevation, unreadable drives show health as unknown
  and the storage score is capped.
- **Report**: *Export report* on the Verdict page saves
  `SysCheck_<computer>_<date>.html`. It needs no internet and no other
  files; use the browser's Print → Save as PDF for a PDF.
- **GPU VRAM**: WMI's `AdapterRAM` is a signed 32-bit field (unreliable above
  4 GB); NVIDIA cards are enriched via `GPUtil` when present.
- **Thermal sensors**: many consumer laptops don't expose a usable thermal
  zone to user space, or expose one that never changes. Either way the test
  reports the sensor as unavailable and falls back to clock-based throttle
  detection. Throttling means the clock held at the end of the run is well
  below the CPU's base clock. Dropping from turbo back to base is normal.
  With neither a sensor nor a throttle signal, thermals are *Not verified*.
  Stopping the test early leaves it out of the grade.
- **Keyboard**: left/right Shift, Ctrl and Alt are told apart by Windows scan
  code. `Fn` is not tested because it never reaches the OS. Hold `Fn` for
  F-keys that default to media functions.
- **Battery discharge**: the laptop must run off the battery, so the step waits
  for the charger to be removed. It then uses the battery's own reported
  discharge rate (`BatteryStatus.DischargeRate`) when the firmware provides
  one. Otherwise it times a *full* 1% step from one whole-percent drop to the
  next. Timing from the unplug would catch only part of a percent and
  overstate the draw. If no full step happens within the timeout, the tool
  falls back to the OS's own `secsleft` estimate (flagged as an estimate).
  Reconnecting AC mid-test voids the reading.
- **Ports test**: an empty port can't be verified by software, so the check is
  driven by live USB arrival/removal events — plug a device into each port in
  turn. HDMI/DisplayPort outputs are listed for reference but not actively
  exercised.

## Third-party software

SysCheck bundles **smartctl** from [smartmontools](https://www.smartmontools.org/)
(version 7.5, official Windows build, unmodified) in `vendor/smartmontools/`.
smartmontools is © Bruce Allen, Christian Franke and contributors, and is
licensed under the GNU GPL v2 or later; see
`vendor/smartmontools/COPYING.txt`. `vendor/smartmontools/SOURCE.txt` lists
the exact build and where to get its source. SysCheck runs `smartctl.exe`
as a separate program and reads its JSON output; it is not linked into
SysCheck.
