"""The activity panel (Trace/Inspector) is available in both modes; it respects
the user's show/hide preference and the toggle button is always visible."""
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
    for _ in range(10):
        qapp.processEvents()


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
    # Isolate settings so toggling the panel never touches the real app prefs.
    win._settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win._panel_pref = True
    win._apply_panel_visibility()
    return win


def _vis(win):
    return (win.right.isVisibleTo(win), win.topbar.panel_toggle.isVisibleTo(win))


def test_panel_visible_in_both_modes(qapp, tmp_path):
    """Panel and its toggle are visible in both Assistant and Workbench modes."""
    win = _make_window(tmp_path, qapp)
    assert _vis(win) == (True, True)            # Assistant default
    win.tabbar.setCurrentIndex(1)               # Workbench
    assert _vis(win) == (True, True)            # still visible in Workbench
    win.tabbar.setCurrentIndex(0)               # back to Assistant
    assert _vis(win) == (True, True)
    win.deleteLater(); _drain(qapp)


def test_user_collapse_pref_persists_across_modes(qapp, tmp_path):
    win = _make_window(tmp_path, qapp)
    win._toggle_panel()                          # user hides it
    assert win.right.isVisibleTo(win) is False
    win.tabbar.setCurrentIndex(1)               # switch to Workbench
    assert win.right.isVisibleTo(win) is False  # still hidden (pref remembered)
    win.tabbar.setCurrentIndex(0)               # back to Assistant
    assert win.right.isVisibleTo(win) is False  # pref still respected
    win._toggle_panel()                          # show again
    assert win.right.isVisibleTo(win) is True
    win.deleteLater(); _drain(qapp)


def test_toggle_works_in_both_modes(qapp, tmp_path):
    """_toggle_panel works in Workbench mode (no longer a no-op)."""
    win = _make_window(tmp_path, qapp)
    win.tabbar.setCurrentIndex(1)               # switch to Workbench
    assert win.right.isVisibleTo(win) is True   # panel visible
    win._toggle_panel()                          # hide it
    assert win.right.isVisibleTo(win) is False
    win._toggle_panel()                          # show it again
    assert win.right.isVisibleTo(win) is True
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
