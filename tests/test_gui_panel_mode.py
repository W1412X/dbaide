"""Workbench shortcuts and mode switching. (The old right-hand activity panel was
removed — trace now travels inline inside each conversation turn — so the former
panel-visibility tests no longer apply.)"""
from __future__ import annotations

import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _drain(qapp, ms=4000):
    QThreadPool.globalInstance().waitForDone(ms)


def _make_window(tmp_path, qapp):
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    win = MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))
    _drain(qapp)  # let bootstrap finish so no worker fires after teardown
    # Isolate settings so the test never touches the real app prefs.
    win._settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    return win


def test_modes_switch_and_composer_visibility(qapp, tmp_path):
    """Switching modes swaps the stacked page; the composer shows only in Assistant."""
    win = _make_window(tmp_path, qapp)
    assert win._current_mode() == "Assistant"
    assert win.composer.isVisibleTo(win) is True
    assert win.sidebar.context_tabs.isVisibleTo(win) is True
    assert win.sidebar.chats.isVisibleTo(win) is True
    assert win.sidebar._schema_panel.isVisibleTo(win) is False
    win.sidebar.context_tabs.setCurrentIndex(1)            # Schema inside Chat
    assert win.sidebar.chats.isVisibleTo(win) is False
    assert win.sidebar._schema_panel.isVisibleTo(win) is True
    win.sidebar.context_tabs.setCurrentIndex(0)            # Chats inside Chat
    win.tabbar.setCurrentIndex(1)               # Workbench
    assert win._current_mode() == "Workbench"
    assert win.composer.isVisibleTo(win) is False
    assert win.sidebar.context_tabs.isVisibleTo(win) is False
    assert win.sidebar.chats.isVisibleTo(win) is False
    assert win.sidebar._schema_panel.isVisibleTo(win) is True
    win.tabbar.setCurrentIndex(0)               # back to Assistant
    assert win.composer.isVisibleTo(win) is True
    assert win.sidebar.context_tabs.isVisibleTo(win) is True
    assert win.sidebar.chats.isVisibleTo(win) is True
    assert win.sidebar._schema_panel.isVisibleTo(win) is False
    win.deleteLater(); _drain(qapp)


def test_no_right_panel(qapp, tmp_path):
    """The right-hand activity panel and its toggle no longer exist."""
    win = _make_window(tmp_path, qapp)
    assert not hasattr(win, "right")
    assert not hasattr(win.topbar, "panel_toggle")
    win.deleteLater(); _drain(qapp)


def test_shortcut_new_query_and_close(qapp, tmp_path):
    win = _make_window(tmp_path, qapp)
    n0 = win.workbench.tabs.count()
    win._shortcut_new_query()
    assert win._current_mode() == "Workbench"
    assert win.workbench.tabs.count() == n0 + 1
    win._shortcut_close_doc()  # closes the just-opened editor
    assert win.workbench.tabs.count() == n0
    win.deleteLater(); _drain(qapp)


def test_shortcut_close_keeps_history_pinned(qapp, tmp_path):
    win = _make_window(tmp_path, qapp)
    win.tabbar.setCurrentIndex(1)
    win.workbench.focus_history()
    before = win.workbench.tabs.count()
    win._shortcut_close_doc()  # History is pinned → no-op
    assert win.workbench.tabs.count() == before
    win.deleteLater(); _drain(qapp)


def test_dashboards_mode_hides_left_sidebar(tmp_path, qapp):
    win = _make_window(tmp_path, qapp)
    names = win._tab_names
    win.tabbar.setCurrentIndex(names.index("Assistant"))
    assert not win.sidebar.isHidden()                 # schema/assets sidebar visible in Assistant
    win.tabbar.setCurrentIndex(names.index("Dashboards"))
    assert win.sidebar.isHidden()                     # irrelevant to dashboards → hidden
    win.tabbar.setCurrentIndex(names.index("Workbench"))
    assert not win.sidebar.isHidden()                 # back on switch away
