# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: DBAide desktop (PyQt6). Build on each target OS separately."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

block_cipher = None

pyqt_datas, pyqt_binaries, pyqt_hidden = collect_all("PyQt6")
# mistune loads its plugins (table, strikethrough, url, …) by string name, so
# PyInstaller's import graph misses them — collect every submodule explicitly or
# Markdown rendering breaks at runtime in the frozen app.
mistune_hidden = collect_submodules("mistune")

hiddenimports = [
    "dbaide",
    "dbaide.cli",
    "dbaide.gui",
    "dbaide.i18n",
    "dbaide.desktop",
    "dbaide.desktop.views",
    "dbaide.desktop.components",
    "dbaide.desktop.dialogs",
    "dbaide.agent",
    "dbaide.agent.loop",
    "dbaide.agent.toolkit",
    "dbaide.assets",
    "dbaide.joins",
    "dbaide.history",
    "dbaide.core",
    "dbaide.tools",
    "dbaide.rendering",
    "dbaide.rendering.markdown",
    "dbaide.adapters",
    "dbaide.adapters.sqlite",
    "dbaide.adapters.mysql",
    "dbaide.adapters.postgres",
    "pymysql",
    "psycopg",
    "psycopg_binary",
    "psycopg_binary._psycopg",
] + pyqt_hidden + mistune_hidden

a = Analysis(
    [str(ROOT / "dbaide" / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=pyqt_binaries,
    datas=pyqt_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
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
    name="DBAide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=sys.platform == "darwin",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DBAide",
)
