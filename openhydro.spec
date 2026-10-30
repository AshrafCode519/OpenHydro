# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Collect all hidden imports for uvicorn and webview
hidden_imports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'starlette',
    'pydantic',
    'httpx',
    'httpcore',
    'websockets',
    'pystray',
    'PIL',
    'yt_dlp',
    'curl_cffi',
    'youtube_transcript_api',
    'youtube_transcript_api.formatters',
    'defusedxml',
    'webview',
    'clr_loader',
    'h11',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets.websockets_sansio_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'wsproto',
]
hidden_imports += collect_submodules('uvicorn')
hidden_imports += collect_submodules('openhydro')
hidden_imports += collect_submodules('youtube_transcript_api')
hidden_imports += collect_submodules('h11')
hidden_imports += collect_submodules('starlette')
hidden_imports += collect_submodules('fastapi')

datas = [
    ('openhydro/web', 'openhydro/web'),
    ('openhydro/extension', 'openhydro/extension'),
    ('assets', 'assets'),
]

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenHydro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # No ugly black console window!
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='OpenHydro',
)

