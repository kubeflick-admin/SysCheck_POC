# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('vendor\\smartmontools\\COPYING.txt', 'vendor\\smartmontools'), ('vendor\\smartmontools\\SOURCE.txt', 'vendor\\smartmontools')]
binaries = [('vendor\\smartmontools\\smartctl.exe', 'vendor\\smartmontools')]
hiddenimports = ['win32timezone']
datas += collect_data_files('cv2')
hiddenimports += collect_submodules('wmi')
hiddenimports += collect_submodules('cpuinfo')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SysCheck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['smartctl.exe'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
