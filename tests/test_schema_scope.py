"""User-provided schema scope: discovery prioritises pinned tables/databases."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _store_with_assets(tmp_path: Path):
    from dbaide.assets import AssetStore, AssetBuilder
    from dbaide.adapters import build_adapter
    from dbaide.joins import JoinCatalogStore
    from dbaide.models import ConnectionConfig
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INT REFERENCES users(id),"
        " amount REAL, status TEXT);"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    store = AssetStore(tmp_path / "assets")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store,
                 join_catalog=JoinCatalogStore(base_dir=tmp_path / "joins")).build(
        profile_mode="none", sample=False)
    return store


def _agent(store):
    from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
    agent = ProgressiveSchemaAgent.__new__(ProgressiveSchemaAgent)
    agent.llm = None  # scoped table seeding needs no LLM
    agent.store = store
    agent.instance = "shop"
    return agent


def test_scoped_table_is_seeded_with_columns(tmp_path):
    agent = _agent(_store_with_assets(tmp_path))
    res = agent.discover("revenue", scope={"tables": [{"database": "main", "table": "orders"}]},
                         column_detail=False)
    tables = {h.table for h in res.hits if h.kind == "table"}
    cols = {h.name for h in res.hits if h.kind == "column" and h.table == "orders"}
    assert "orders" in tables
    assert {"amount", "status", "user_id"} <= cols
    assert res.trace and "user-provided schema scope" in res.trace[0]


def test_find_table_database(tmp_path):
    agent = _agent(_store_with_assets(tmp_path))
    assert agent._find_table_database("users") == "main"
    assert agent._find_table_database("nonexistent") == ""


def test_bare_table_name_resolves_database(tmp_path):
    agent = _agent(_store_with_assets(tmp_path))
    res = agent.discover("x", scope={"tables": [{"table": "users"}]}, column_detail=False)
    seeded = [h for h in res.hits if h.kind == "table" and h.table == "users"]
    assert seeded and seeded[0].database == "main"


def test_build_attached_scope(qapp_unused=None):
    # Pure helper on MainWindow — test the parsing logic in isolation.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dbaide.desktop.views.main_window import MainWindow
    scope = MainWindow._build_attached_scope(None, [
        {"kind": "database", "path": "shop.main", "name": "main"},
        {"kind": "table", "path": "shop.main.orders", "name": "orders"},
        {"kind": "table", "path": "shop.main.orders", "name": "orders"},  # dup
    ])
    assert scope["databases"] == ["main"]
    assert scope["tables"] == [{"database": "main", "table": "orders"}]
