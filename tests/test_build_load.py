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


def test_light_mode_only_profiles_key_columns(tmp_path):
    db = tmp_path / "b.db"
    _make_db(db)
    conn = ConnectionConfig(name="lightc", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    store = AssetStore(tmp_path / "assets")
    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build(profile_mode="light")
    # Only the PK (id) qualifies; name/score are skipped.
    assert stats.profiled_columns == 1
    assert stats.skipped_profiles == 2


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
