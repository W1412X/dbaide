"""Backup metadata registry — SQLite store tracking all backup versions."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DIR = Path("~/.dbaide/backups").expanduser()

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS backups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    connection  TEXT    NOT NULL,
    database    TEXT    NOT NULL DEFAULT '',
    "table"     TEXT    NOT NULL DEFAULT '',
    timestamp   TEXT    NOT NULL,
    format      TEXT    NOT NULL,
    row_count   INTEGER NOT NULL DEFAULT 0,
    file_size   INTEGER NOT NULL DEFAULT 0,
    file_path   TEXT    NOT NULL,
    scope       TEXT    NOT NULL DEFAULT 'table'
);
"""


@dataclass
class BackupRecord:
    id: int
    connection: str
    database: str
    table: str
    timestamp: str
    format: str
    row_count: int
    file_size: int
    file_path: str
    scope: str


class BackupRegistry:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _DEFAULT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.base_dir / "registry.db"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        conn.execute(_SCHEMA)
        conn.commit()
        conn.close()

    def record(self, *, connection: str, database: str, table: str,
               fmt: str, row_count: int, file_size: int, file_path: str,
               scope: str = "table") -> int:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._connect()
        try:
            cur = conn.execute(
                'INSERT INTO backups (connection, database, "table", timestamp, format, '
                "row_count, file_size, file_path, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (connection, database, table, ts, fmt, row_count, file_size, file_path, scope),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
        finally:
            conn.close()

    def list_backups(self, *, connection: str = "", database: str = "",
                     table: str = "") -> list[BackupRecord]:
        clauses, params = [], []
        if connection:
            clauses.append("connection = ?")
            params.append(connection)
        if database:
            clauses.append("database = ?")
            params.append(database)
        if table:
            clauses.append('"table" = ?')
            params.append(table)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f'SELECT id, connection, database, "table", timestamp, format, '
                f"row_count, file_size, file_path, scope FROM backups{where} "
                f"ORDER BY id DESC",
                params,
            ).fetchall()
            return [BackupRecord(**dict(r)) for r in rows]
        finally:
            conn.close()

    def get(self, backup_id: int) -> BackupRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT id, connection, database, "table", timestamp, format, '
                "row_count, file_size, file_path, scope FROM backups WHERE id = ?",
                (backup_id,),
            ).fetchone()
            return BackupRecord(**dict(row)) if row else None
        finally:
            conn.close()

    def delete(self, backup_id: int) -> bool:
        rec = self.get(backup_id)
        if rec is None:
            return False
        path = Path(rec.file_path)
        if path.exists():
            path.unlink()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
            conn.commit()
        finally:
            conn.close()
        return True

    def backup_dir(self, connection: str, database: str = "", table: str = "") -> Path:
        parts = [connection]
        if database:
            parts.append(database)
        if table:
            parts.append(table)
        d = self.base_dir / os.sep.join(parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def close(self) -> None:
        pass
