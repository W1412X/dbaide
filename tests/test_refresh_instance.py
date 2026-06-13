"""P2: refresh_instance re-syncs the base layer with the live catalog and applies
the agreed change strategies — preserving notes for surviving objects and
cascade-deleting notes for objects that are actually gone."""

from __future__ import annotations

import sqlite3

from dbaide.annotations import AnnotationStore
from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService


def _service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_ASSETS", str(tmp_path / "assets"))
    monkeypatch.setenv("DBAIDE_ANNOTATIONS", str(tmp_path / "annotations"))
    return DesktopService(ConfigManager(tmp_path / "config.toml"))


def test_refresh_applies_diff_and_cascades_notes(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL, obsolete TEXT);
        """
    )
    c.commit(); c.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "shop", "type": "sqlite", "path": str(db)})
    svc.dispatch("project_instance", {"name": "shop"})

    ann = AnnotationStore()
    ann.add("shop", scope="database", note="prod db", database="main")
    ann.add("shop", scope="table", note="current orders", database="main", table="orders")
    ann.add("shop", scope="table", note="retired table", database="main", table="users")       # → table dropped
    ann.add("shop", scope="column", note="net amount", database="main", table="orders", column="amount")   # survives
    ann.add("shop", scope="column", note="retired col", database="main", table="orders", column="obsolete")  # → column dropped

    # Mutate the live schema: drop users, add products, change orders (drop `obsolete`,
    # add `note`, change `amount` REAL→BIGINT).
    c = sqlite3.connect(db)
    c.executescript(
        """
        DROP TABLE users;
        CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT);
        DROP TABLE orders;
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount BIGINT, note TEXT);
        """
    )
    c.commit(); c.close()

    result = svc.dispatch("refresh_instance", {"name": "shop"})
    assert result["removed_tables"] == 1 and result["added_tables"] == 1 and result["changed_tables"] == 1

    # Tree reflects the new structure.
    tree = svc.dispatch("schema_tree", {"name": "shop"})
    tables = {t["name"] for d in tree for t in d["children"]}
    assert tables == {"orders", "products"}

    store = AssetStore()
    odoc = store.table_doc("shop", "main", "orders")
    cols = {col["name"] for col in odoc["columns"]}
    assert cols == {"id", "amount", "note"} and "obsolete" not in cols
    assert odoc.get("base_fingerprint")  # changed table re-fingerprinted
    # The denormalized column_count must track the new structure (not stay stale).
    assert odoc.get("column_count") == 3
    trow = next(t for d in svc.dispatch("schema_tree", {"name": "shop"})
                for t in d["children"] if t["name"] == "orders")
    assert trow["column_count"] == 3
    assert store.table_doc("shop", "main", "users") is None  # dropped doc removed

    # Notes: surviving objects kept, gone objects cascade-deleted.
    notes = {(r["scope"], r.get("table"), r.get("column")): r["note"] for r in ann.list_records("shop")}
    assert ("database", "", "") in notes                       # db note kept (db still exists)
    assert ("table", "orders", "") in notes                    # orders table note kept
    assert ("column", "orders", "amount") in notes             # surviving column note kept
    assert ("table", "users", "") not in notes                 # dropped table → note cascaded
    assert ("column", "orders", "obsolete") not in notes       # dropped column → note cascaded


def test_refresh_marks_enrichment_stale_on_structural_change(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v REAL); INSERT INTO t VALUES (1, 2.0);")
    c.commit(); c.close()

    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "s", "type": "sqlite", "path": str(db)})
    svc.dispatch("project_instance", {"name": "s"})
    svc.dispatch("enrich_table", {"name": "s", "database": "main", "table": "t"})  # gives it sample_rows

    store = AssetStore()
    assert store.table_doc("s", "main", "t").get("sample_rows")
    assert not store.table_doc("s", "main", "t").get("enrichment_stale")

    # Structural change → enrichment kept but flagged stale.
    c = sqlite3.connect(db)
    c.executescript("DROP TABLE t; CREATE TABLE t (id INTEGER PRIMARY KEY, v BIGINT, extra TEXT);")
    c.commit(); c.close()
    svc.dispatch("refresh_instance", {"name": "s"})

    doc = store.table_doc("s", "main", "t")
    assert doc.get("enrichment_stale") is True
    assert "extra" in {col["name"] for col in doc["columns"]}

    # The tree surfaces the status so the UI can mark it.
    trow = next(t for d in svc.dispatch("schema_tree", {"name": "s"}) for t in d["children"] if t["name"] == "t")
    assert trow["stale"] is True


def test_schema_tree_status_flags(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE a (id INTEGER PRIMARY KEY); CREATE TABLE b (id INTEGER PRIMARY KEY);"
                    "INSERT INTO b VALUES (1);")
    c.commit(); c.close()
    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "s", "type": "sqlite", "path": str(db)})
    svc.dispatch("project_instance", {"name": "s"})        # both base
    svc.dispatch("enrich_table", {"name": "s", "database": "main", "table": "b"})  # b enriched

    rows = {t["name"]: t for d in svc.dispatch("schema_tree", {"name": "s"}) for t in d["children"]}
    assert rows["a"]["enriched"] is False and rows["a"]["stale"] is False  # base only
    assert rows["b"]["enriched"] is True and rows["b"]["stale"] is False    # enriched


def test_schema_asset_summary_failed_when_errors_and_no_tables(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    sqlite3.connect(db).close()
    svc = _service(tmp_path, monkeypatch)
    svc.dispatch("save_connection", {"name": "s", "type": "sqlite", "path": str(db)})
    store = svc.store
    conn = svc.cfg.get_connection("s")
    store.write_json(store.instance_dir("s") / "instance.json", {
        "name": "s",
        "stats": {"errors": ["discover failed"]},
        **store.connection_metadata(conn),
    })
    summary = svc._schema_asset_summary("s", conn, [])
    assert summary["state"] == "failed"
    assert summary["errors"] == 1
