"""Shared helpers for the agent tool handlers (see dbaide.agent.toolkit)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.db.identifiers import normalize_db_table_for_dialect
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.toolkit")


def _string_list(value: Any) -> list[str]:
    """Normalize model-provided string/list arguments.

    LLMs sometimes return a single string for an array field. Treat delimited
    strings as several items and an undelimited string as one item; never iterate
    a string character-by-character.
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        for sep in ("\n", ";", "；", "，", ","):
            text = text.replace(sep, ",")
        return [item.strip() for item in text.split(",") if item.strip()]
    return []


def _relations_payload(relations: list[dict[str, Any]]) -> dict[str, Any]:
    declared = sum(1 for r in relations if str(r.get("source") or "") in {"foreign_key", "agent", "user"})
    semantic = sum(1 for r in relations if r.get("source") == "semantic")
    catalog = sum(1 for r in relations if r.get("catalog") or str(r.get("source") or "") in {"user", "agent"})
    validated = sum(1 for r in relations if _safe_float(r.get("confidence") or 0, 0.0) >= 0.35)
    return {
        "relations": relations,
        "count": len(relations),
        "declared_count": declared,
        "semantic_count": semantic,
        "catalog_count": catalog,
        "validated_count": validated,
    }


def _targets_from_relations(orchestrator: AskOrchestrator, relations: list[dict[str, Any]]) -> list[tuple[str, str]]:
    db_default = orchestrator.run_state.table_database or orchestrator.run_state.database or ""
    names: set[str] = set()
    for rel in relations:
        for key in ("table", "ref_table"):
            name = str(rel.get(key) or "").strip()
            if name:
                names.add(name)
    targets: list[tuple[str, str]] = []
    for name in sorted(names):
        db = db_default
        for schema_key, schema_db in orchestrator.run_state.schema_db.items():
            table_part = orchestrator.run_state.schema_table_part(schema_key, schema_db)
            if table_part == name or schema_key == name:
                db = schema_db
                break
        targets.append((db, name))
    return targets


def _note_working_db(orchestrator: AskOrchestrator, database: str) -> None:
    """Record the database the agent has narrowed into, so subsequent tools default
    to *where the tables were found* — not the connection's default database — when
    the model omits the ``database`` argument. Never overwrite a known working db
    with an empty one."""
    db = (database or "").strip()
    if db:
        orchestrator.run_state.note_working_database(db)


def _normalize_tool_table(orchestrator: AskOrchestrator, table: str, database: str = "") -> tuple[str, str]:
    """Normalize a model-provided table reference at a tool boundary.

    MySQL/MariaDB use ``database.table`` for cross-database qualification, so an
    explicit dotted reference must override the current working database. Postgres
    uses dotted ``schema.table`` inside the connected database, so it keeps the
    existing generic normalization behavior.
    """
    dialect = str(getattr(orchestrator.adapter, "dialect", "") or "").lower()
    return normalize_db_table_for_dialect(table, database, dialect)


def _remember_table_schema(orchestrator: AskOrchestrator, table: str, database: str, columns: list[ColumnInfo]) -> None:
    orchestrator.run_state.remember_table_schema(table, database, columns)
    orchestrator.schema.context.record_columns(
        table, columns, instance=orchestrator.instance, database=database,
    )


def _disclosed_table_names(orchestrator: AskOrchestrator) -> list[str]:
    return orchestrator.run_state.disclosed_table_names()


def _find_schema_columns(orchestrator: AskOrchestrator, table: str, database: str) -> list[ColumnInfo] | None:
    return orchestrator.run_state.find_schema_columns(table, database)


def _collect_disclosed_schemas(
    orchestrator: AskOrchestrator,
    args: dict[str, Any],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    database_default = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
    tables_arg = args.get("tables")
    selected: list[tuple[str, str, list[ColumnInfo]]] = []

    table_names = _string_list(tables_arg)
    if table_names:
        for name in table_names:
            db, name = _normalize_tool_table(orchestrator, name, database_default)
            columns = _find_schema_columns(orchestrator, name, db)
            if columns is None:
                columns = orchestrator.schema.describe_table(name, database=db)
                if columns:
                    _remember_table_schema(orchestrator, name, db, columns)
            if columns:
                selected.append((db, name, columns))
        return selected

    if orchestrator.run_state.schemas:
        for key, columns in orchestrator.run_state.schemas.items():
            db = orchestrator.run_state.schema_db.get(key, database_default)
            table = orchestrator.run_state.schema_table_part(key, db)
            selected.append((db, table, columns))
        return selected

    table = str(args.get("table") or orchestrator.run_state.table or "").strip()
    if not table:
        return []
    db = str(args.get("database") or orchestrator.run_state.table_database or database_default)
    db, table = _normalize_tool_table(orchestrator, table, db)
    columns = _find_schema_columns(orchestrator, table, db)
    if columns is None:
        columns = orchestrator.schema.describe_table(table, database=db)
        if columns:
            _remember_table_schema(orchestrator, table, db, columns)
    if not columns:
        return []
    return [(db, table, columns)]


def _requested_table_names(args: dict[str, Any]) -> list[str]:
    return _string_list(args.get("tables"))


def _requested_table_labels(orchestrator: AskOrchestrator, args: dict[str, Any]) -> list[str]:
    database_default = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
    labels: list[str] = []
    for raw in _requested_table_names(args):
        db, table = _normalize_tool_table(orchestrator, raw, database_default)
        labels.append(f"{db}.{table}" if db else table)
    return labels


def _ambiguous_requested_tables(orchestrator: AskOrchestrator, args: dict[str, Any]) -> dict[str, list[str]]:
    """Bare table names are ambiguous when the loop has disclosed the same table in
    multiple databases. Do not silently pick the current working database; ask the
    model to specify database.table so the selected context is explicit."""
    if str(args.get("database") or "").strip():
        return {}
    out: dict[str, list[str]] = {}
    for raw in _requested_table_names(args):
        if "." in raw:
            continue
        labels: list[str] = []
        for schema_key, database in orchestrator.run_state.schema_db.items():
            table = orchestrator.run_state.schema_table_part(schema_key, database)
            if table == raw:
                labels.append(schema_key)
        if len(set(labels)) > 1:
            out[raw] = sorted(set(labels))
    return out


def _err(stage: str, message: str, *, retryable: bool = False) -> DBAideError:
    return DBAideError(
        code=ErrorCode.VALIDATION_FAILED,
        stage=stage,
        message=message,
        retryable=retryable,
    )


# ── Safe type conversion helpers ──────────────────────────────────────────
# LLMs sometimes pass non-numeric strings for int/float parameters (e.g.
# "large", "high"). Bare int()/float() would raise ValueError and crash the
# agent loop. These wrappers absorb bad input silently.


def _safe_int(value: Any, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tables_in_sql(sql: str) -> list[str]:
    from dbaide.validation.sql_cleanup import strip_function_from_keywords

    # Strip FROM inside SQL functions (EXTRACT, TRIM, SUBSTRING) so that
    # column names are not mistaken for table references.
    cleaned = strip_function_from_keywords(sql)
    tokens = cleaned.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]()').strip()
            if table and table.lower() not in {"select", "where", ""} and not table.startswith("(") and table not in tables:
                tables.append(table)
    return tables
