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

