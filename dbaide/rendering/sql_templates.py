"""Generate starter SQL for a table — DBeaver's "Generate SQL" context action.

Templates are built from the schema columns already in memory, with dialect-aware
identifier quoting. SELECT variants are directly runnable under the read-only
guard; INSERT/UPDATE are generated for copy-and-edit (they won't pass the guard).
"""
from __future__ import annotations

from typing import Any

from dbaide.adapters.base import quote_identifier


def _q(name: str, dialect: str) -> str:
    return quote_identifier(str(name or ""), dialect)


def _col_names(columns: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("name") or "") for c in (columns or []) if c.get("name")]


def select_star(table: str, columns: list[dict[str, Any]], dialect: str = "generic",
                limit: int = 100) -> str:
    return f"SELECT * FROM {_q(table, dialect)}\nLIMIT {int(limit)};"


def select_columns(table: str, columns: list[dict[str, Any]], dialect: str = "generic",
                   limit: int = 100) -> str:
    names = _col_names(columns)
    if not names:
        return select_star(table, columns, dialect, limit)
    cols = ",\n  ".join(_q(n, dialect) for n in names)
    return f"SELECT\n  {cols}\nFROM {_q(table, dialect)}\nLIMIT {int(limit)};"


def count_rows(table: str, columns: list[dict[str, Any]], dialect: str = "generic") -> str:
    return f"SELECT COUNT(*) FROM {_q(table, dialect)};"


def insert_template(table: str, columns: list[dict[str, Any]], dialect: str = "generic") -> str:
    names = _col_names(columns)
    if not names:
        return f"INSERT INTO {_q(table, dialect)} () VALUES ();"
    cols = ", ".join(_q(n, dialect) for n in names)
    placeholders = ", ".join(f":{n}" for n in names)
    return f"INSERT INTO {_q(table, dialect)} ({cols})\nVALUES ({placeholders});"


def update_template(table: str, columns: list[dict[str, Any]], dialect: str = "generic") -> str:
    names = _col_names(columns)
    pk = next((str(c.get("name")) for c in (columns or []) if c.get("primary_key")), None)
    assigns = ",\n  ".join(f"{_q(n, dialect)} = :{n}" for n in names) or "-- columns"
    where = f"{_q(pk, dialect)} = :{pk}" if pk else "-- condition"
    return f"UPDATE {_q(table, dialect)} SET\n  {assigns}\nWHERE {where};"


# kind → (label-key, builder)
TEMPLATES = {
    "select_star": select_star,
    "select_columns": select_columns,
    "count": count_rows,
    "insert": insert_template,
    "update": update_template,
}


def generate(kind: str, table: str, columns: list[dict[str, Any]],
             dialect: str = "generic") -> str:
    builder = TEMPLATES.get(kind, select_star)
    return builder(table, columns, dialect)
