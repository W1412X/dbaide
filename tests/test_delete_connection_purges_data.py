"""Deleting a connection must remove ALL its per-connection data, not just the
config entry (regression: 'fake delete' left assets/joins/notes/sessions/logs/history)."""

from __future__ import annotations

import sqlite3

from dbaide.annotations import AnnotationStore
from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.history.session_store import ChatSessionStore
from dbaide.history.store import WorkflowHistoryStore
from dbaide.joins import JoinCatalogStore
from dbaide.observability import query_log


def _service(tmp_path, monkeypatch):
    # Redirect every per-connection store under tmp_path so the test is hermetic.
    monkeypatch.setenv("DBAIDE_ASSETS", str(tmp_path / "assets"))
    monkeypatch.setenv("DBAIDE_JOINS", str(tmp_path / "joins"))
    monkeypatch.setenv("DBAIDE_ANNOTATIONS", str(tmp_path / "annotations"))
    monkeypatch.setenv("DBAIDE_LOG_DIR", str(tmp_path / "logs"))
    cfg = ConfigManager(path=tmp_path / "config.toml")
    svc = DesktopService(cfg)
    svc.sessions = ChatSessionStore(base_dir=tmp_path / "sessions")
    svc.history = WorkflowHistoryStore(base_dir=tmp_path / "history")
    return svc


def test_delete_connection_purges_all_per_connection_data(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    name = "demo"
    db = tmp_path / "demo.db"
    sqlite3.connect(db).close()
    svc.dispatch("save_connection", {"name": name, "type": "sqlite", "path": str(db)})

    # Seed data in every per-connection store.
    AssetStore().write_json(AssetStore().instance_dir(name) / "instance.json", {"kind": "instance"})
    JoinCatalogStore().add(name, {"table": "a", "column": "x", "ref_table": "b", "ref_column": "y"}, source="user")
    AnnotationStore().add(name, scope="table", note="n", table="orders")
    svc.sessions.create(name, title="s")
    ql = query_log.for_instance(name)
    ql.path.parent.mkdir(parents=True, exist_ok=True)
    ql.path.write_text('{"sql":"SELECT 1"}\n', encoding="utf-8")

    # Pre-conditions: data exists.
    assert AssetStore().instance_dir(name).exists()
    assert JoinCatalogStore().instance_path(name).exists()
    assert AnnotationStore().instance_path(name).exists()
    assert svc.sessions._conn_dir(name).exists()
    assert ql.path.exists()

    result = svc.dispatch("delete_connection", {"name": name})
    assert result["deleted"] == name

    # Post-conditions: every store is purged.
    assert not AssetStore().instance_dir(name).exists()
    assert not JoinCatalogStore().instance_path(name).parent.exists()
    assert not AnnotationStore().instance_path(name).parent.exists()
    assert not svc.sessions._conn_dir(name).exists()
    assert not query_log.purge_instance(name) and not ql.path.exists()
    # config entry gone too
    assert name not in {c["name"] for c in (svc.bootstrap().get("connections") or [])}
