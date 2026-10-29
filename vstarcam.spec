# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Recopilar todos los submódulos de vstarcam
hidden = collect_submodules('vstarcam')
hidden += ['webbrowser', 'json', 'logging', 'threading', 'http.server']

# Detección de objetos opcional: si numpy/onnxruntime están instalados al
# compilar, se incluyen en el ejecutable. Si no, el .exe funciona sin ellos.
try:
    ort_datas, ort_binaries, ort_hidden = collect_all('onnxruntime')
except Exception:
    ort_datas, ort_binaries, ort_hidden = [], [], []
hidden += ort_hidden

a = Analysis(
    ['vstarcam/__main__.py'],
    pathex=['.'],
    binaries=ort_binaries,
    datas=[
        ('web', 'web'),
        ('bin', 'bin'),
    ] + ort_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='vstarcam',
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
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='vstarcam',
)
