"""Composer context attachment — cascade, dedup, and chip rendering."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_add_attachment_dedup(qapp):
    from dbaide.desktop.components.composer import ComposerWidget
    c = ComposerWidget()
    assert c.add_attachment(kind="table", path="c.db.t", name="t") is True
    assert c.add_attachment(kind="table", path="c.db.t", name="t") is False  # dup
    assert len(c.attachments()) == 1


def test_chips_visible_and_clearable(qapp):
    from dbaide.desktop.components.composer import ComposerWidget
    c = ComposerWidget()
    assert c._chips_host.isHidden()
    c.add_attachment(kind="database", path="c.db", name="db")
    assert not c._chips_host.isHidden()
    c.clear_attachments()
    assert c._chips_host.isHidden()
    assert c.attachments() == []


def test_remove_one_attachment(qapp):
    from dbaide.desktop.components.composer import ComposerWidget
    c = ComposerWidget()
    c.add_attachment(kind="database", path="c.db", name="db")
    c.add_attachment(kind="table", path="c.db.t", name="t")
    c._remove_attachment("c.db.t")
    paths = [a["path"] for a in c.attachments()]
    assert paths == ["c.db"]


def _make_window(tmp_path, qapp):
    import sqlite3
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig
    db = tmp_path / "app.db"
    cx = sqlite3.connect(db)
    cx.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); "
                     "CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INT REFERENCES users(id));")
    cx.commit(); cx.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    return MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))


def test_attach_table_cascades_database_no_dup(qapp, tmp_path):
    from PyQt6.QtCore import QThreadPool
    win = _make_window(tmp_path, qapp)
    QThreadPool.globalInstance().waitForDone(3000)
    for _ in range(6):
        qapp.processEvents()
    win.service.build_assets({"name": "local", "profile_mode": "none"})
    win.schema_rows = win.service.schema_tree({"name": "local"})
    tables = {n["name"]: n for n in win.schema_rows[0]["children"]}
    win._attach_node(tables["orders"])
    kinds = [a["kind"] for a in win.composer.attachments()]
    assert "database" in kinds and "table" in kinds  # cascade
    win._attach_node(tables["users"])  # second table, same db
    db_count = sum(1 for a in win.composer.attachments() if a["kind"] == "database")
    assert db_count == 1  # no duplicate database
    win.deleteLater()
    QThreadPool.globalInstance().waitForDone(2000)
    for _ in range(6):
        qapp.processEvents()
