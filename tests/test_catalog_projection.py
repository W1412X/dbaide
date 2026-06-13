"""P1: the catalog projection (`project_instance`) builds the BASE schema document
from the live catalog alone — structure/keys/FKs/indexes — with NO LLM summaries,
NO data sampling and NO column profiling. It is the schema tree's source and does
not require the heavy `build_assets` enrichment."""

from __future__ import annotations

import sqlite3

from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService


def _service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_ASSETS", str(tmp_path / "assets"))
    cfg = ConfigManager(tmp_path / "config.toml")
    return DesktopService(cfg)


def test_project_instance_builds_base_from_catalog(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount REAL,
            paid_at INTEGER
        );
        INSERT INTO users VALUES (1, 'a');
        INSERT INTO orders VALUES (1, 1, 2.0, 100);
        """
    )
    conn.commit(); conn.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "shop", "type": "sqlite", "path": str(db)})

    # No assets yet → tree is empty (the gap we're fixing).
    assert svc.dispatch("schema_tree", {"name": "shop"}) == []

    # Project from the live catalog (no model configured → no LLM either way).
    events = []
    svc.dispatch("project_instance", {"name": "shop", "progress": events.append})
    assert any(isinstance(e, dict) and e.get("stage") == "build_assets" for e in events)

    tree = svc.dispatch("schema_tree", {"name": "shop"})
    assert tree[0]["asset_summary"]["state"] == "base"
    assert tree[0]["asset_summary"]["profile_state"] == "on_demand"
    tables = {t["name"] for db in tree for t in db["children"]}
    assert {"users", "orders"} <= tables

    store = AssetStore()
    odoc = store.table_doc("shop", "main", "orders")
    assert odoc is not None
    # Base structural fields ARE present (catalog-derivable).
    colnames = {c["name"] for c in odoc.get("columns", [])}
    assert {"id", "user_id", "amount", "paid_at"} <= colnames
    assert odoc.get("foreign_keys"), "FKs come from the catalog"
    # Enrichment fields are NOT built (no sampling / no profiling at base layer).
    assert not odoc.get("sample_rows")
    assert odoc.get("row_count_exact") is False
    assert all("profile" not in c and "semantic_summary" not in c for c in odoc.get("columns", []))


def test_schema_tree_can_read_database_before_instance_finishes(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL);
        """
    )
    conn.commit(); conn.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "shop", "type": "sqlite", "path": str(db)})
    snapshots: list[list[dict]] = []

    def on_progress(event):
        if (
            isinstance(event, dict)
            and event.get("node_id") == "build:db:main"
            and event.get("status") == "completed"
        ):
            snapshots.append(svc.dispatch("schema_tree", {"name": "shop"}))

    svc.dispatch("project_instance", {"name": "shop", "progress": on_progress})

    assert snapshots, "database completion should be visible before the final instance doc is written"
    tables = {t["name"] for db_row in snapshots[0] for t in db_row["children"]}
    assert tables == {"users", "orders"}


def test_enrich_table_is_granular_and_preserves_others(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL);
        INSERT INTO users VALUES (1, 'a');
        INSERT INTO orders VALUES (1, 2.0), (2, 3.0);
        """
    )
    conn.commit(); conn.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "shop", "type": "sqlite", "path": str(db)})
    svc.dispatch("project_instance", {"name": "shop"})  # base only, no samples

    store = AssetStore()
    assert not store.table_doc("shop", "main", "orders").get("sample_rows")
    assert not store.table_doc("shop", "main", "users").get("sample_rows")

    # Enrich just `orders` (sampling on). `users` must stay base-only and present.
    svc.dispatch("enrich_table", {"name": "shop", "database": "main", "table": "orders"})

    assert store.table_doc("shop", "main", "orders").get("sample_rows"), "enriched table gets samples"
    assert not store.table_doc("shop", "main", "users").get("sample_rows"), "other table preserved as base"
    # Both tables still in the rollup / tree (granular build didn't drop the rest).
    tree = svc.dispatch("schema_tree", {"name": "shop"})
    assert tree[0]["asset_summary"]["state"] == "partial"
    assert tree[0]["asset_summary"]["sampled_tables"] == 1
    tables = {t["name"] for d in tree for t in d["children"]}
    assert {"users", "orders"} <= tables


def test_refresh_single_table_updates_structure_without_dropping_others(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL);
        """
    )
    conn.commit(); conn.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "shop", "type": "sqlite", "path": str(db)})
    svc.dispatch("project_instance", {"name": "shop"})

    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE orders ADD COLUMN status TEXT")
    conn.commit(); conn.close()

    result = svc.dispatch("refresh_instance", {"name": "shop", "database": "main", "table": "orders"})
    assert result["changed_tables"] == 1

    store = AssetStore()
    orders = store.table_doc("shop", "main", "orders")
    users = store.table_doc("shop", "main", "users")
    assert {c["name"] for c in orders.get("columns", [])} >= {"id", "amount", "status"}
    assert users is not None

    tree = svc.dispatch("schema_tree", {"name": "shop"})
    tables = {t["name"] for d in tree for t in d["children"]}
    assert tables == {"users", "orders"}
