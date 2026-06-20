# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[('lib\\cspstat64.dll', '.')] if os.path.exists('lib\\cspstat64.dll') else [],
    datas=[('models\\face_landmarker.task', 'models')],
    hiddenimports=['eventlet.hubs.epolls', 'eventlet.hubs.kqueue', 'eventlet.hubs.selects', 'engineio.async_drivers.threading', 'engineio.async_drivers.eventlet', 'simple_websocket', 'wsproto', 'serial.tools.list_ports'] + collect_submodules('dns'),
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
    name='TomatoBackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TomatoBackend',
)
