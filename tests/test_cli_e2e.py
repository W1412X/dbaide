import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            status TEXT,
            created_at TEXT
        );
        INSERT INTO users VALUES
            (1, 'a@example.com', 'active', '2026-01-01'),
            (2, 'b@example.com', 'disabled', '2026-01-02');
        """
    )
    conn.commit()
    conn.close()


def run_cli(tmp_path, *args):
    env = {
        **os.environ,
        "DBAIDE_CONFIG": str(tmp_path / "config.toml"),
        "DBAIDE_ASSETS": str(tmp_path / "assets"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "dbaide.cli", *args],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_cli_connection_asset_search_and_sql_flow(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)

    run_cli(tmp_path, "connect", "add", "local", "--type", "sqlite", "--path", str(db), "--default", "--top-k", "5", "--sample-limit", "10")
    status = run_cli(tmp_path, "assets", "status", "local")
    hits = json.loads(run_cli(tmp_path, "find", "email", "--conn", "local", "--json"))
    sql = run_cli(tmp_path, "sql", "--conn", "local", "select email from users")

    assert "local" in status
    assert "ready" in status
    assert any(hit["path"] == "local.main.users.email" for hit in hits)
    assert "LIMIT" in sql
