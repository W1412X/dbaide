"""Shared helpers for the agent tool handlers (see dbaide.agent.toolkit)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.toolkit")


def _relations_payload(relations: list[dict[str, Any]]) -> dict[str, Any]:
    declared = sum(1 for r in relations if str(r.get("source") or "") in {"foreign_key", "agent", "user"})
    semantic = sum(1 for r in relations if r.get("source") == "semantic")
    catalog = sum(1 for r in relations if r.get("catalog") or str(r.get("source") or "") in {"user", "agent"})
    validated = sum(1 for r in relations if float(r.get("confidence") or 0) >= 0.35)
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
            table_part = _schema_table_part(schema_key, schema_db)
            if table_part == name or schema_key == name:
                db = schema_db
                break
        targets.append((db, name))
    return targets


def _schema_key(database: str, table: str) -> str:
    db = database.strip()
    return f"{db}.{table}" if db else table


def _schema_table_part(schema_key: str, database: str = "") -> str:
    db = str(database or "").strip()
    key = str(schema_key or "").strip()
    prefix = f"{db}."
    if db and key.startswith(prefix):
        return key[len(prefix):]
    return key


def _note_working_db(orchestrator: AskOrchestrator, database: str) -> None:
    """Record the database the agent has narrowed into, so subsequent tools default
    to *where the tables were found* — not the connection's default database — when
    the model omits the ``database`` argument. Never overwrite a known working db
    with an empty one."""
    db = (database or "").strip()
    if db:
        orchestrator.run_state.table_database = db


def _remember_table_schema(orchestrator: AskOrchestrator, table: str, database: str, columns: list[ColumnInfo]) -> None:
    key = _schema_key(database, table)
    orchestrator.run_state.schemas[key] = columns
    orchestrator.run_state.schema_db[key] = database
    orchestrator.run_state.table = table
    _note_working_db(orchestrator, database)
    orchestrator.run_state.columns = columns


def _disclosed_table_names(orchestrator: AskOrchestrator) -> list[str]:
    names: list[str] = []
    for key in orchestrator.run_state.schemas:
        names.append(_schema_table_part(key, orchestrator.run_state.schema_db.get(key, "")))
    return names


def _find_schema_columns(orchestrator: AskOrchestrator, table: str, database: str) -> list[ColumnInfo] | None:
    key = _schema_key(database, table)
    if key in orchestrator.run_state.schemas:
        return orchestrator.run_state.schemas[key]
    matches: list[list[ColumnInfo]] = []
    for schema_key, columns in orchestrator.run_state.schemas.items():
        schema_db = orchestrator.run_state.schema_db.get(schema_key, "")
        table_part = _schema_table_part(schema_key, schema_db)
        database_ok = not database or schema_db == database
        if database_ok and (schema_key == table or table_part == table or table_part.endswith(f".{table}")):
            matches.append(columns)
    if len(matches) == 1:
        return matches[0]
    return None


def _collect_disclosed_schemas(
    orchestrator: AskOrchestrator,
    args: dict[str, Any],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    database_default = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
    tables_arg = args.get("tables")
    selected: list[tuple[str, str, list[ColumnInfo]]] = []

    if isinstance(tables_arg, list) and tables_arg:
        from dbaide.agent.schema_context import normalize_db_table

        for raw in tables_arg:
            name = str(raw).strip()
            if not name:
                continue
            db, name = normalize_db_table(name, database_default)
            columns = _find_schema_columns(orchestrator, name, db)
            if columns is None:
                columns = orchestrator.schema.describe_table(name, database=db)
                _remember_table_schema(orchestrator, name, db, columns)
            if columns:
                selected.append((db, name, columns))
        return selected

    if orchestrator.run_state.schemas:
        for key, columns in orchestrator.run_state.schemas.items():
            db = orchestrator.run_state.schema_db.get(key, database_default)
            table = _schema_table_part(key, db)
            selected.append((db, table, columns))
        return selected

    table = str(args.get("table") or orchestrator.run_state.table or "").strip()
    if not table:
        return []
    from dbaide.agent.schema_context import normalize_db_table

    db = str(args.get("database") or orchestrator.run_state.table_database or database_default)
    db, table = normalize_db_table(table, db)
    columns = _find_schema_columns(orchestrator, table, db)
    if columns is None:
        columns = orchestrator.schema.describe_table(table, database=db)
        _remember_table_schema(orchestrator, table, db, columns)
    if not columns:
        return []
    return [(db, table, columns)]


def _err(stage: str, message: str, *, retryable: bool = False) -> DBAideError:
    return DBAideError(
        code=ErrorCode.VALIDATION_FAILED,
        stage=stage,
        message=message,
        retryable=retryable,
    )


def _tables_in_sql(sql: str) -> list[str]:
    tokens = sql.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]')
            if table and table.lower() not in {"select", "where"} and table not in tables:
                tables.append(table)
    return tables
