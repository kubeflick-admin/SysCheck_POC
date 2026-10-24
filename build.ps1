# ─── SysCheck POC :: Windows build script ──────────────────────────────
# Produces a single windowed .exe in .\dist\SysCheck.exe
#
# Usage (from the project root, in PowerShell):
#   python -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   pip install -r requirements.txt pyinstaller
#   .\build.ps1
#
# NOTE: run the resulting .exe "As Administrator" for full S.M.A.R.T.
#       (power-on hours / host writes) and ACPI thermal readings.
#
# smartctl.exe (smartmontools, GPL) is bundled with its licence and source
# note; keep this list in step with SysCheck.spec, which this script
# regenerates. UPX is kept off smartctl.exe so the official binary ships
# unmodified.

$ErrorActionPreference = "Stop"

pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name SysCheck `
    --collect-submodules wmi `
    --collect-submodules cpuinfo `
    --collect-data cv2 `
    --hidden-import win32timezone `
    --add-binary "vendor\smartmontools\smartctl.exe;vendor\smartmontools" `
    --add-data "vendor\smartmontools\COPYING.txt;vendor\smartmontools" `
    --add-data "vendor\smartmontools\SOURCE.txt;vendor\smartmontools" `
    --upx-exclude smartctl.exe `
    main.py

Write-Host ""
Write-Host "Build complete -> .\dist\SysCheck.exe" -ForegroundColor Green
