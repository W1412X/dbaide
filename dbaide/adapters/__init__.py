from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.adapters.mysql import MySQLAdapter
from dbaide.adapters.postgres import PostgresAdapter
from dbaide.adapters.sqlite import SQLiteAdapter
from dbaide.models import ConnectionConfig


def build_adapter(config: ConnectionConfig, *, policy=None, caller: str = "agent") -> DatabaseAdapter:
    """Construct an adapter wired to its resource policy and caller tag.

    ``policy`` defaults to the connection's ``load_profile`` preset (production
    when unset). All build/agent/gui queries through this adapter share one
    QueryBudget and QueryLog keyed by ``config.name``.
    """
    typ = config.type.lower().strip()
    if typ == "sqlite":
        adapter: DatabaseAdapter = SQLiteAdapter(config)
    elif typ in {"mysql", "mariadb"}:
        adapter = MySQLAdapter(config)
    elif typ in {"postgres", "postgresql"}:
        adapter = PostgresAdapter(config)
    else:
        raise ValueError(f"Unsupported connection type: {config.type}")
    adapter.attach_resources(policy=policy, caller=caller)
    return adapter


__all__ = ["DatabaseAdapter", "build_adapter"]

