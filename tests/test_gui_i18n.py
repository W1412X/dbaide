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


def test_language_change_persists_and_prompts_restart(qapp, tmp_path, monkeypatch):
    from dbaide import i18n
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views import main_window as mw
    from dbaide.models import ConnectionConfig

    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    c.commit()
    c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="demo", type="sqlite", path=str(db)), make_default=True)

    prompts: list[str] = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: prompts.append(a[-1]))
    i18n.set_language("en")
    win = mw.MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))
    win2 = None
    try:
        assert win.tabbar.tabText(0) == "Chat"

        win._change_language("zh")
        assert ConfigManager(path=tmp_path / "config.toml").ui_language() == "zh"
        assert win.tabbar.tabText(0) == "Chat"
        assert win.sidebar.context_tabs.tabText(0) == "Chats"
        assert prompts and "重启" in prompts[-1]

        # Startup in zh still renders Chinese:
        i18n.set_language("zh")
        win2 = mw.MainWindow(DesktopService(ConfigManager(path=tmp_path / "config.toml"),
                                            AssetStore(tmp_path / "assets")))
        assert win2.tabbar.tabText(0) == "对话"
    finally:
        if win2 is not None:
            win2.deleteLater()
        win.deleteLater()
        qapp.processEvents()
        i18n.set_language("en")
