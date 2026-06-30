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


def test_optimize_dialog_renders_markdown(qapp):
    from dbaide.desktop.dialogs.sql_optimize_dialog import SqlOptimizeDialog
    dlg = SqlOptimizeDialog("- add an index on **orders.status**\n- avoid `SELECT *`")
    txt = dlg.view.toPlainText().lower()
    assert "index" in txt and "select *" in txt


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
