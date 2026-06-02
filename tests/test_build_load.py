"""Build-load tests: tiered profiling, dry-run, no full-table sorts."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.assets.profiler import ColumnProfiler
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.observability import query_log


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, score REAL);
        INSERT INTO users VALUES (1,'a',1.0),(2,'b',2.0),(3,'c',3.0);
        """
    )
    conn.commit()
    conn.close()


def test_profiler_emits_no_random_order(tmp_path):
    db = tmp_path / "a.db"
    _make_db(db)
    adapter = build_adapter(ConnectionConfig(name="noRand", type="sqlite", path=str(db)), caller="build")
    profiler = ColumnProfiler(adapter)
    profiler.profile("users", ColumnInfo(name="name", data_type="text"))
    sqls = " ".join(e.sql.lower() for e in query_log.for_instance("noRand").recent())
    assert "random()" not in sqls
    assert "rand()" not in sqls
    assert "order by" not in sqls or "group by" in sqls  # only top-K group-by may order


def test_dry_run_estimates_without_profiling(tmp_path):
    db = tmp_path / "c.db"
    _make_db(db)
    conn = ConnectionConfig(name="dry", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    store = AssetStore(tmp_path / "assets")
    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build(profile_mode="auto", dry_run=True)
    assert stats.estimated_queries > 0
    # Dry-run must not run profiling SQL: only cheap metadata + zero profiling scans.
    recent = query_log.for_instance("dry").recent()
    profiling_sqls = [e for e in recent if "group by" in e.sql.lower()]
    assert profiling_sqls == []


def test_big_table_drops_to_light(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _make_db(db)
    conn = ConnectionConfig(name="big", type="sqlite", path=str(db))
    adapter = build_adapter(conn)

    # Force the table to look huge so it crosses big_table_rows.
    from dbaide.models import TableInfo
    real_list = adapter.list_tables

    def fake_list(database=""):
        out = []
        for t in real_list(database=database):
            out.append(TableInfo(name=t.name, estimated_rows=10_000_000, table_type=t.table_type))
        return out

    monkeypatch.setattr(adapter, "list_tables", fake_list)
    store = AssetStore(tmp_path / "assets")
    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build(profile_mode="auto")
    assert stats.light_tables == 1
    # No COUNT(DISTINCT) / top-K on the big table.
    sqls = " ".join(e.sql.lower() for e in query_log.for_instance("big").recent())
    assert "count(distinct" not in sqls


def test_build_emits_structured_trace_events(tmp_path):
    """Build progress is a tree: a 'build:root' tool node + a per-database node."""
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    for t in ("customers", "orders", "items"):
        conn.execute(f"CREATE TABLE {t}(id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()
    c = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    events = []
    AssetBuilder(connection=c, adapter=build_adapter(c, caller="build"),
                 store=AssetStore(tmp_path / "assets"),
                 progress=lambda m: events.append(m)).build(profile_mode="auto")

    dicts = [e for e in events if isinstance(e, dict)]
    assert dicts and all(d.get("stage") == "build_assets" for d in dicts)
    # Root node present (tool), and a per-database node parented to it.
    assert any(d.get("node_id") == "build:root" and d.get("kind") == "tool" for d in dicts)
    db_nodes = [d for d in dicts if d.get("node_id") == "build:db:main"]
    assert db_nodes and all(d.get("parent_id") == "build:root" for d in db_nodes)
    # A live per-table progress line and a completed database line.
    assert any("describing" in d["title"] or "/3 tables" in d["title"] for d in db_nodes)
    assert any(d.get("status") == "completed" and "columns" in d["title"] for d in db_nodes)
    # The final root summary carries the totals.
    root_done = [d for d in dicts if d.get("node_id") == "build:root" and d.get("status") in ("completed", "failed")]
    assert root_done and "tables" in root_done[-1]["title"] and "queries" in root_done[-1]["title"]
