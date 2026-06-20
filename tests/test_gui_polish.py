from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_data_browser_filter_completer_popup_is_themed(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser

    browser = DataBrowser()
    popup = browser._filter_completer.popup()
    css = popup.styleSheet()
    assert "QScrollBar:vertical" in css
    assert "QListView::item:hover" in css


def test_main_window_statusbar_disables_native_size_grip(qapp, tmp_path):
    import sqlite3

    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    win = MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))
    assert win.statusbar.isSizeGripEnabled() is False


def test_primary_compact_button_uses_disabled_icon_variant(qapp):
    from PyQt6.QtGui import QIcon

    from dbaide.desktop.components.base import compact_button

    btn = compact_button("全部安装", primary=True, width=100)
    icon = btn.icon()
    normal = icon.pixmap(14, 14, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(14, 14, QIcon.Mode.Disabled).toImage()
    assert not normal.isNull()
    assert not disabled.isNull()
    assert normal != disabled
