"""Database object identifier helpers shared across layers.

This module is intentionally below agent, desktop, and tools.  Anything that
needs to interpret user/model-provided ``database.table`` references should use
these helpers instead of importing agent context code.
"""

from __future__ import annotations


def normalize_db_table(table: str, database: str = "") -> tuple[str, str]:
    """Split a db-qualified table name into ``(database, table)``.

    Generic normalization preserves ``schema.table`` when a database is already
    known. That is useful for Postgres, where a table value may legitimately be
    schema-qualified inside the connected database.
    """

    def _clean(value: str) -> str:
        return str(value or "").strip().strip('`"[]').strip()

    table = _clean(table)
    database = _clean(database)
    if "." in table:
        prefix, rest = table.split(".", 1)
        prefix, rest = _clean(prefix), _clean(rest)
        if prefix and rest:
            if not database or prefix == database:
                return prefix, rest
            return database, f"{prefix}.{rest}"
    return database, table


def normalize_db_table_for_dialect(table: str, database: str = "", dialect: str = "") -> tuple[str, str]:
    """Dialect-aware table normalization at public/service/tool boundaries.

    MySQL/MariaDB use ``database.table`` for cross-database qualification, so an
    explicit dotted reference should override the working database. Other
    dialects keep the generic behavior.
    """

    text = str(table or "").strip()
    if str(dialect or "").lower() in {"mysql", "mariadb"} and "." in text:
        return normalize_db_table(text, "")
    return normalize_db_table(text, database)
