# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: DBAide CLI only (no PyQt6). Smaller bundle."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]

block_cipher = None

hiddenimports = [
    "dbaide",
    "dbaide.cli",
    "dbaide.agent",
    "dbaide.assets",
    "dbaide.joins",
    "dbaide.history",
    "dbaide.core",
    "dbaide.tools",
    "dbaide.adapters",
    "dbaide.adapters.sqlite",
    "dbaide.adapters.mysql",
    "dbaide.adapters.postgres",
    "pymysql",
    "psycopg",
    "psycopg_binary",
    "psycopg_binary._psycopg",
]

a = Analysis(
    [str(ROOT / "dbaide" / "cli.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt6", "PyQt5", "matplotlib", "numpy", "pandas"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dbaide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
