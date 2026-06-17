"""Tests for the backup module: writers, registry, and engine."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from dbaide.backup.registry import BackupRecord, BackupRegistry
from dbaide.backup.writers import CsvWriter, SqliteWriter, SqlWriter
from dbaide.models import ColumnInfo


# ── Writer tests ──────────────────────────────────────────────────────────────


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path


def _sample_rows(n: int = 5) -> list[dict]:
    return [{"id": i, "name": f"item_{i}", "value": i * 1.5} for i in range(1, n + 1)]


def _col_infos() -> list[ColumnInfo]:
    return [
        ColumnInfo("id", "INTEGER", primary_key=True),
        ColumnInfo("name", "VARCHAR(100)"),
        ColumnInfo("value", "DECIMAL(10,2)"),
    ]


def test_csv_writer(out_dir):
    path = out_dir / "test.csv"
    cols = ["id", "name", "value"]
    w = CsvWriter(path, cols)
    w.write_rows(_sample_rows(3))
    total = w.close()
    assert total == 3
    assert w.file_size > 0
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["id", "name", "value"]
        rows = list(reader)
        assert len(rows) == 3
        assert rows[0][1] == "item_1"


def test_csv_writer_multiple_batches(out_dir):
    path = out_dir / "batches.csv"
    w = CsvWriter(path, ["id", "name", "value"])
    w.write_rows(_sample_rows(2))
    w.write_rows(_sample_rows(3))
    total = w.close()
    assert total == 5


def test_sql_writer(out_dir):
    path = out_dir / "test.sql"
    cols = ["id", "name", "value"]
    w = SqlWriter(path, cols, _col_infos(), dialect="generic", table_name="items")
    w.write_rows(_sample_rows(2))
    total = w.close()
    assert total == 2
    content = path.read_text()
    assert "CREATE TABLE" in content
    assert "INSERT INTO" in content
    assert "'item_1'" in content


def test_sql_writer_null_and_bool(out_dir):
    path = out_dir / "special.sql"
    cols = ["a", "b", "c"]
    w = SqlWriter(path, cols, table_name="t", dialect="generic")
    w.write_rows([{"a": None, "b": True, "c": False}])
    w.close()
    content = path.read_text()
    assert "NULL" in content
    assert "TRUE" in content
    assert "FALSE" in content


def test_sql_writer_escapes_quotes(out_dir):
    path = out_dir / "esc.sql"
    w = SqlWriter(path, ["x"], table_name="t", dialect="generic")
    w.write_rows([{"x": "it's a test"}])
    w.close()
    content = path.read_text()
    assert "it''s a test" in content


def test_sqlite_writer(out_dir):
    path = out_dir / "test.sqlite"
    cols = ["id", "name", "value"]
    w = SqliteWriter(path, cols, _col_infos(), table_name="items")
    w.write_rows(_sample_rows(4))
    total = w.close()
    assert total == 4
    conn = sqlite3.connect(str(path))
    rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()
    assert rows[0] == 4
    row = conn.execute("SELECT name FROM items WHERE id = 2").fetchone()
    assert row[0] == "item_2"
    conn.close()


def test_sqlite_writer_type_mapping(out_dir):
    path = out_dir / "types.sqlite"
    infos = [
        ColumnInfo("a", "BIGINT"),
        ColumnInfo("b", "DOUBLE"),
        ColumnInfo("c", "BLOB"),
        ColumnInfo("d", "VARCHAR(255)"),
    ]
    w = SqliteWriter(path, ["a", "b", "c", "d"], infos, table_name="t")
    w.close()
    conn = sqlite3.connect(str(path))
    info = conn.execute("PRAGMA table_info(t)").fetchall()
    types = {r[1]: r[2] for r in info}
    assert types["a"] == "INTEGER"
    assert types["b"] == "REAL"
    assert types["c"] == "BLOB"
    assert types["d"] == "TEXT"
    conn.close()


# ── Registry tests ────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path):
    return BackupRegistry(base_dir=tmp_path / "backups")


def test_registry_record_and_list(registry):
    bid = registry.record(
        connection="local", database="main", table="orders",
        fmt="csv", row_count=100, file_size=2048,
        file_path="/tmp/test.csv",
    )
    assert bid >= 1
    records = registry.list_backups()
    assert len(records) == 1
    assert records[0].connection == "local"
    assert records[0].table == "orders"
    assert records[0].row_count == 100


def test_registry_filter(registry):
    registry.record(connection="a", database="db1", table="t1",
                    fmt="csv", row_count=10, file_size=100, file_path="/tmp/a.csv")
    registry.record(connection="b", database="db2", table="t2",
                    fmt="sql", row_count=20, file_size=200, file_path="/tmp/b.sql")
    assert len(registry.list_backups(connection="a")) == 1
    assert len(registry.list_backups(connection="b")) == 1
    assert len(registry.list_backups(database="db1")) == 1
    assert len(registry.list_backups(table="t2")) == 1
    assert len(registry.list_backups()) == 2


def test_registry_delete(registry, tmp_path):
    fpath = tmp_path / "to_delete.csv"
    fpath.write_text("dummy")
    bid = registry.record(connection="x", database="d", table="t",
                          fmt="csv", row_count=1, file_size=5,
                          file_path=str(fpath))
    assert registry.delete(bid) is True
    assert not fpath.exists()
    assert len(registry.list_backups()) == 0


def test_registry_delete_nonexistent(registry):
    assert registry.delete(999) is False


def test_registry_get(registry):
    bid = registry.record(connection="c", database="d", table="t",
                          fmt="sqlite", row_count=5, file_size=100,
                          file_path="/tmp/t.sqlite")
    rec = registry.get(bid)
    assert rec is not None
    assert rec.format == "sqlite"
    assert registry.get(999) is None


def test_registry_backup_dir(registry):
    d = registry.backup_dir("conn1", "db1", "tbl1")
    assert d.exists()
    assert "conn1" in str(d)


# ── Engine integration test (uses SQLite adapter) ────────────────────────────


def test_engine_backup_table(tmp_path):
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
    conn.executemany("INSERT INTO items VALUES (?, ?, ?)",
                     [(i, f"item_{i}", i * 9.99) for i in range(1, 51)])
    conn.commit()
    conn.close()

    from dbaide.backup import BackupEngine, BackupRegistry
    from dbaide.models import ConnectionConfig

    cfg = ConnectionConfig(name="test_conn", type="sqlite", path=str(db_path))
    registry = BackupRegistry(base_dir=tmp_path / "backups")
    engine = BackupEngine(cfg, registry)

    progress_calls = []
    def on_progress(table, done, total):
        progress_calls.append((table, done, total))

    result = engine.backup_table("", "items", fmt="csv", batch_size=20,
                                 on_progress=on_progress)

    assert result["row_count"] == 50
    assert result["table"] == "items"
    assert result["format"] == "csv"
    assert Path(result["file_path"]).exists()
    assert len(progress_calls) >= 2  # at least 2 batches of 20 + 1 of 10

    records = registry.list_backups()
    assert len(records) == 1
    assert records[0].row_count == 50


def test_engine_backup_table_sql_format(tmp_path):
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE users (id INTEGER, email TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)",
                     [(1, "a@b.c"), (2, "d@e.f")])
    conn.commit()
    conn.close()

    from dbaide.backup import BackupEngine, BackupRegistry
    from dbaide.models import ConnectionConfig

    cfg = ConnectionConfig(name="test", type="sqlite", path=str(db_path))
    registry = BackupRegistry(base_dir=tmp_path / "backups")
    engine = BackupEngine(cfg, registry)
    result = engine.backup_table("", "users", fmt="sql", batch_size=100)
    content = Path(result["file_path"]).read_text()
    assert "CREATE TABLE" in content
    assert "INSERT INTO" in content
    assert "a@b.c" in content


def test_engine_backup_table_sqlite_format(tmp_path):
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE nums (id INTEGER, val REAL)")
    conn.executemany("INSERT INTO nums VALUES (?, ?)", [(i, i * 0.1) for i in range(10)])
    conn.commit()
    conn.close()

    from dbaide.backup import BackupEngine, BackupRegistry
    from dbaide.models import ConnectionConfig

    cfg = ConnectionConfig(name="test", type="sqlite", path=str(db_path))
    registry = BackupRegistry(base_dir=tmp_path / "backups")
    engine = BackupEngine(cfg, registry)
    result = engine.backup_table("", "nums", fmt="sqlite", batch_size=100)

    out_conn = sqlite3.connect(result["file_path"])
    count = out_conn.execute("SELECT COUNT(*) FROM nums").fetchone()[0]
    assert count == 10
    out_conn.close()


def test_engine_backup_database(tmp_path):
    db_path = tmp_path / "multi.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE a (x INTEGER)")
    conn.execute("CREATE TABLE b (y TEXT)")
    conn.executemany("INSERT INTO a VALUES (?)", [(i,) for i in range(5)])
    conn.executemany("INSERT INTO b VALUES (?)", [("hello",), ("world",)])
    conn.commit()
    conn.close()

    from dbaide.backup import BackupEngine, BackupRegistry
    from dbaide.models import ConnectionConfig

    cfg = ConnectionConfig(name="test", type="sqlite", path=str(db_path))
    registry = BackupRegistry(base_dir=tmp_path / "backups")
    engine = BackupEngine(cfg, registry)
    results = engine.backup_database("", fmt="csv", batch_size=100, threads=2)
    tables_backed = {r["table"] for r in results if not r.get("error")}
    assert "a" in tables_backed
    assert "b" in tables_backed
    total_rows = sum(r["row_count"] for r in results if not r.get("error"))
    assert total_rows == 7
