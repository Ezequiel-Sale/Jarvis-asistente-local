# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


a = Analysis(
    ['jarvis_app.py'],
    pathex=[],
    binaries=[],
    # collect_data_files('openwakeword') incluye los modelos de «Hey Jarvis»
    # (descargados con openwakeword.utils.download_models; ver README).
    datas=[('assets/icon.ico', 'assets'), ('init_db.sql', '.'),
           # Sensores de temperatura (LibreHardwareMonitor, MPL 2.0) y sus dependencias.
           ('vendor/LibreHardwareMonitor', 'vendor/LibreHardwareMonitor')]
    + collect_data_files('openwakeword') + collect_data_files('pythonnet')
    + collect_data_files('clr_loader'),
    hiddenimports=['mss.windows', 'av', 'dxcam', 'pystray._win32', 'pystray._util.win32',
                   'winrt.windows.media.control', 'winrt.windows.foundation',
                   # Interfaces COM de UI Automation ya generadas (clic en pantalla).
                   'uiautomation', *collect_submodules('comtypes.gen'),
                   'pythonnet', 'clr', 'clr_loader', *collect_submodules('clr_loader')],
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
    [],
    exclude_binaries=True,
    name='Jarvis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Jarvis',
)
