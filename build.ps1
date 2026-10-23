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
    main.py

Write-Host ""
Write-Host "Build complete -> .\dist\SysCheck.exe" -ForegroundColor Green
