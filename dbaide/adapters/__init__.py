from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.adapters.mysql import MySQLAdapter
from dbaide.adapters.postgres import PostgresAdapter
from dbaide.adapters.sqlite import SQLiteAdapter
from dbaide.models import ConnectionConfig


def build_adapter(config: ConnectionConfig) -> DatabaseAdapter:
    typ = config.type.lower().strip()
    if typ == "sqlite":
        return SQLiteAdapter(config)
    if typ in {"mysql", "mariadb"}:
        return MySQLAdapter(config)
    if typ in {"postgres", "postgresql"}:
        return PostgresAdapter(config)
    raise ValueError(f"Unsupported connection type: {config.type}")


__all__ = ["DatabaseAdapter", "build_adapter"]

