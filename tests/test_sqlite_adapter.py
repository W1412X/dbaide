import sqlite3

from dbaide.adapters import build_adapter
from dbaide.models import ConnectionConfig


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total_amount REAL,
            status TEXT,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        INSERT INTO users VALUES (1, 'Alice', '2026-01-01'), (2, 'Bob', '2026-01-02');
        INSERT INTO orders VALUES
            (1, 1, 10.5, 'paid', '2026-01-03'),
            (2, 1, 20.0, 'pending', '2026-01-04'),
            (3, 2, 30.0, 'paid', '2026-01-04');
        """
    )
    conn.commit()
    conn.close()


def test_sqlite_adapter_schema_and_profile(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="local", type="sqlite", path=str(db)))
    tables = adapter.list_tables()
    assert {t.name for t in tables} == {"users", "orders"}
    columns = adapter.describe_table("orders")
    assert "total_amount" in {c.name for c in columns}
    profile = adapter.profile_column("orders", "status")
    assert profile.row_count == 3
    assert profile.distinct_count == 2



def test_profile_column_light_skips_distinct_and_topk(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="local", type="sqlite", path=str(db)))
    profile = adapter.profile_column("orders", "status", heavy_scan=False)
    # No COUNT(DISTINCT) / GROUP BY top-K on big tables.
    assert profile.distinct_count is None
    assert profile.top_values == []
    assert profile.row_count == 3  # cheap COUNT(*) still available


def test_profile_column_merges_avg_and_length(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="local", type="sqlite", path=str(db)))
    numeric = adapter.profile_column("orders", "total_amount", include_avg=True)
    assert numeric.numeric_stats.get("avg") is not None
    text = adapter.profile_column("orders", "status", include_length=True)
    assert "avg_length" in text.text_stats


def test_every_query_is_audited(tmp_path):
    from dbaide.observability import query_log
    db = tmp_path / "app.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="audit", type="sqlite", path=str(db)), caller="build")
    log = query_log.for_instance("audit")
    adapter.execute_readonly("SELECT * FROM users", limit=10)
    adapter.profile_column("orders", "status")
    entries = log.recent()
    assert len(entries) >= 2
    assert all(e.caller == "build" for e in entries)
    assert any("users" in e.sql for e in entries)


def test_budget_caps_concurrency(tmp_path):
    import threading
    from dbaide.db.policy import ResourcePolicy
    db = tmp_path / "app.db"
    make_db(db)
    policy = ResourcePolicy.for_load_profile("production").merged_with({"max_inflight_queries": 2})
    adapter = build_adapter(ConnectionConfig(name="cap", type="sqlite", path=str(db)), policy=policy)
    assert adapter.budget.max_inflight == 2

    def worker():
        adapter.execute_readonly("SELECT 1", limit=1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert adapter.budget.stats.peak_inflight <= 2


def test_execute_preserves_duplicate_column_names(tmp_path):
    """A join selecting same-named columns from both sides (users.id, orders.id) must
    keep ALL columns — duplicates are disambiguated, not collapsed by dict keying."""
    db = tmp_path / "dup.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="dup", type="sqlite", path=str(db)))
    r = adapter.execute_readonly(
        "SELECT users.id, orders.id, users.name FROM users JOIN orders ON orders.user_id = users.id "
        "WHERE orders.id = 1",
        limit=10,
    )
    assert r.columns == ["id", "id (2)", "name"]
    assert r.rows == [{"id": 1, "id (2)": 1, "name": "Alice"}]


def test_execute_empty_result_keeps_column_names(tmp_path):
    """A zero-row result still reports its columns (driven by the cursor description,
    not the first row), so the UI/agent can see the shape."""
    db = tmp_path / "empty.db"
    make_db(db)
    adapter = build_adapter(ConnectionConfig(name="empty", type="sqlite", path=str(db)))
    r = adapter.execute_readonly("SELECT id, name FROM users WHERE id < 0", limit=10)
    assert r.rows == []
    assert r.columns == ["id", "name"]
