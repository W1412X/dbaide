"""GUI/service tests for the Workbench SQL optimization advice."""

from __future__ import annotations

import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    from dbaide.desktop.service import DesktopService
    return DesktopService()


def test_sqltab_shows_optimization_inline(qapp):
    from dbaide.desktop.views.sql_tab import SqlTab
    tab = SqlTab()
    tab.show_optimization({"suggestions": "- add an index on **orders.status**\n- avoid `SELECT *`"})
    assert tab.tabs.currentWidget() is tab.advice          # switches to the inline Advice tab
    txt = tab.advice.toPlainText().lower()
    assert "index" in txt and "select *" in txt
    tab.show_optimization({"error": "no_model"})            # no model → clear inline message
    assert "model" in tab.advice.toPlainText().lower()


def test_optimize_sql_action_reports_no_model(qapp, service, tmp_path):
    from dbaide.models import ConnectionConfig
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, status TEXT)")
    con.commit(); con.close()
    service.cfg.upsert_connection(ConnectionConfig(name="a", type="sqlite", path=str(db)), make_default=True)
    out = service.dispatch("optimize_sql", {"connection_name": "a", "sql": "SELECT * FROM t"})
    assert out["error"] == "no_model"        # no model configured → clear signal, not a crash
    out2 = service.dispatch("optimize_sql", {"connection_name": "a", "sql": "   "})
    assert out2.get("error")                  # empty SQL rejected


def test_settings_resource_save_preserves_config_only_string_keys(qapp, tmp_path):
    import dbaide.desktop.views.main_window  # noqa: F401 — establish import order
    from dbaide.desktop.dialogs.settings import SettingsDialog
    dlg = SettingsDialog(connections=[], models=[], config_dir=str(tmp_path), initial_page="resources")
    dlg._resource_values = {"optimizer_model": "fast"}   # a config-only key with no widget here
    captured: dict = {}
    dlg.resource_saved.connect(lambda d: captured.update(d))
    dlg._save_resources()
    assert captured["values"].get("optimizer_model") == "fast"   # GUI save must not wipe it
