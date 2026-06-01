"""Offscreen test: GUI language switch updates labels, persists, drives model language."""

from __future__ import annotations

import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_language_switch_updates_ui_and_config(qapp, tmp_path):
    from dbaide import i18n
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    c.commit()
    c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="demo", type="sqlite", path=str(db)), make_default=True)
    i18n.set_language("en")
    win = MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))
    assert win.tabbar.tabText(0) == "Ask"

    win._change_language("zh")
    assert win.tabbar.tabText(0) == "提问"
    assert ConfigManager(path=tmp_path / "config.toml").ui_language() == "zh"   # persisted
    assert "Chinese" in i18n.answer_language_directive()                         # model follows

    win._change_language("en")
    assert win.tabbar.tabText(0) == "Ask"
    i18n.set_language("en")
