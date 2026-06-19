# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: DBAide desktop (PyQt6). Build on each target OS separately."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
ICON_DIR = ROOT / "packaging" / "icons"
# Per-OS app icon: .ico for the Windows EXE, .icns for the macOS .app bundle.
ICON = str(ICON_DIR / ("dbaide.ico" if sys.platform == "win32" else "dbaide.icns"))

block_cipher = None
# Do not strip Qt/WebEngine shared libraries — macOS/Linux strip can break SIP
# bindings and cause "cannot import type … from PyQt6.QtCore" at runtime.
STRIP = False

_runtime_hooks = []
if sys.platform == "linux":
    _runtime_hooks.append(str(ROOT / "packaging" / "pyinstaller" / "rthook_linux_libpath.py"))

# mistune loads its plugins (table, strikethrough, url, …) by string name, so
# PyInstaller's import graph misses them — collect every submodule explicitly or
# Markdown rendering breaks at runtime in the frozen app.
mistune_hidden = collect_submodules("mistune")

# The app uses a small, explicit set of Qt modules. We deliberately do NOT collect_all("PyQt6")
# (that force-bundles the entire Qt — QtQml/Quick/Network/Pdf/translations/… — and
# bloats the package ~3-4x). PyInstaller's built-in PyQt6 hooks bundle just these
# modules + the platform plugins they need.
certifi_datas = collect_data_files("certifi")
desktop_datas = collect_data_files("dbaide.desktop")

# WebEngine ships Chromium + QtWebEngineProcess helper outside the normal PyQt6
# widget hook graph. collect_all ensures frameworks/DLLs/resources land in the
# frozen bundle with matching layout (especially QtWebEngineCore.framework on macOS).
_webengine_datas: list = []
_webengine_binaries: list = []
_webengine_hidden: list = []
for _mod in (
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebChannel",
):
    _d, _b, _h = collect_all(_mod)
    _webengine_datas += _d
    _webengine_binaries += _b
    _webengine_hidden += _h

hiddenimports = [
    "certifi",
    "dbaide",
    "dbaide.ssl_certs",
    "dbaide.cli",
    "dbaide.gui",
    "dbaide.i18n",
    "dbaide.desktop",
    "dbaide.desktop.views",
    "dbaide.desktop.components",
    "dbaide.desktop.components.chart_block",
    "dbaide.desktop.dialogs",
    "dbaide.agent",
    "dbaide.agent.loop",
    "dbaide.agent.toolkit",
    "dbaide.assets",
    "dbaide.joins",
    "dbaide.history",
    "dbaide.core",
    "dbaide.tools",
    "dbaide.charts",
    "dbaide.agent.chart_agent",
    "dbaide.agent.toolkit.chart_tools",
    "dbaide.adapters",
    "dbaide.adapters.sqlite",
    "dbaide.adapters.mysql",
    "dbaide.adapters.postgres",
    "pymysql",
    "psycopg",
    "psycopg_binary",
    "psycopg_binary._psycopg",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtNetwork",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebChannel",
    "PyQt6.QtPrintSupport",  # required transitively by QtWebEngineWidgets
] + mistune_hidden + _webengine_hidden

# Drop big Qt modules we never import, so nothing transitively drags them in.
_QT_EXCLUDES = [
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets", "PyQt6.QtQuick3D",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtWebSockets", "PyQt6.QtDataVisualization",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtSql", "PyQt6.QtTest",
    "PyQt6.QtDesigner", "PyQt6.QtUiTools", "PyQt6.QtHelp", "PyQt6.QtBluetooth",
    "PyQt6.QtNfc", "PyQt6.QtPositioning", "PyQt6.QtSensors", "PyQt6.QtSerialPort",
    "PyQt6.QtRemoteObjects", "PyQt6.QtScxml", "PyQt6.QtSpatialAudio",
    "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets",
    "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.Qt3DExtras",
]

a = Analysis(
    [str(ROOT / "dbaide" / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=_webengine_binaries,
    datas=desktop_datas + certifi_datas + _webengine_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=_runtime_hooks,
    excludes=["tkinter", "matplotlib", "numpy", "pandas"] + _QT_EXCLUDES,
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
    strip=STRIP,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=sys.platform == "darwin",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=STRIP,
    upx=False,
    upx_exclude=[],
    name="DBAide",
)

# On macOS, wrap the collected bundle into a proper .app so it can be
# dragged into /Applications. Other platforms keep the plain dist/DBAide folder.
if sys.platform == "darwin":
    import os
    app = BUNDLE(
        coll,
        name="DBAide.app",
        icon=str(ICON_DIR / "dbaide.icns"),
        bundle_identifier="dev.dbaide.app",
        version=os.environ.get("DBAIDE_VERSION", "0.0.0"),
        info_plist={
            "CFBundleName": "DBAide",
            "CFBundleDisplayName": "DBAide",
            "CFBundleShortVersionString": os.environ.get("DBAIDE_VERSION", "0.0.0"),
            "CFBundleVersion": os.environ.get("DBAIDE_VERSION", "0.0.0"),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
