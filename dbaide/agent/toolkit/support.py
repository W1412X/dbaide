"""Shared helpers for the agent tool handlers (see dbaide.agent.toolkit)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.models import ColumnInfo
from dbaide.agent.progress_events import subagent_event

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.toolkit")


_CATEGORICAL_TYPES = ("char", "text", "string", "enum", "varchar", "nchar", "nvarchar", "tinytext")


def _persist_agent_joins(
    orchestrator: AskOrchestrator,
    relations: list[dict[str, Any]],
    *,
    database: str = "",
) -> None:
    catalog = getattr(orchestrator, "join_catalog", None)
    if catalog is None:
        return
    try:
        saved = catalog.persist_agent_candidates(
            orchestrator.instance,
            relations,
            database=database or orchestrator.run_state.database or "",
        )
        if saved:
            orchestrator.progress(
                subagent_event(
                    agent="join_catalog",
                    title=f"Saved {len(saved)} join candidate(s)",
                    parent="get_relations",
                ),
            )
    except Exception as exc:
        logger.warning("persist_agent_joins_failed: %s", exc)


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
            table_part = schema_key.split(".", 1)[1] if "." in schema_key else schema_key
            if table_part == name or schema_key == name:
                db = schema_db
                break
        targets.append((db, name))
    return targets


def _schema_key(database: str, table: str) -> str:
    db = database.strip()
    return f"{db}.{table}" if db else table


def _note_working_db(orchestrator: AskOrchestrator, database: str) -> None:
    """Record the database the agent has narrowed into, so subsequent tools default
    to *where the tables were found* — not the connection's default database — when
    the model omits the ``database`` argument. Never overwrite a known working db
    with an empty one."""
    db = (database or "").strip()
    if db:
        orchestrator.run_state.table_database = db


def _sample_observed_values(
    orchestrator: AskOrchestrator,
    disclosed: list[tuple[str, str, list[ColumnInfo]]],
    *,
    max_columns: int = 6,
    sample_rows: int = 300,
    max_distinct: int = 30,
    max_candidates: int = 12,
) -> dict[str, list[str]]:
    """Best-effort: the real distinct values of low-cardinality text columns in the
    resolved schema, so the clarifier asks about ACTUAL value encodings (e.g. which
    `delivery_status` means "妥投") instead of guessing one. Bounded and never fatal:
    reads a small sample (not a full DISTINCT scan), caps columns/rows, swallows
    errors, and is skipped when execution isn't allowed.

    The per-column sample reads run CONCURRENTLY (bounded) — they're independent
    SELECT … LIMIT probes, so doing them sequentially just stacked latency before
    every clarification."""
    if not orchestrator.run_state.execute_allowed:
        return {}
    candidates: list[tuple[str, str, str]] = []  # (db, table, column)
    for db, table, columns in disclosed:
        for col in columns:
            dtype = (getattr(col, "data_type", "") or "").lower()
            name = getattr(col, "name", "")
            if not name or not name.replace("_", "").isalnum():
                continue  # only plain identifiers (no quoting headaches across dialects)
            if not any(k in dtype for k in _CATEGORICAL_TYPES):
                continue
            candidates.append((db, table, name))
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
    if not candidates:
        return {}

    def _probe(item: tuple[str, str, str]) -> tuple[str, list[str] | None]:
        db, table, name = item
        qualified = f"{db}.{table}" if db else table
        try:
            result = orchestrator.query.execute_sql(
                f"SELECT {name} FROM {qualified} LIMIT {sample_rows}",
                database=db, limit=sample_rows,
            )
        except Exception:  # noqa: BLE001 — grounding is optional
            return f"{table}.{name}", None
        seen: list[str] = []
        for row in getattr(result, "rows", []) or []:
            v = row.get(name) if isinstance(row, dict) else None
            if v is None:
                continue
            s = str(v)
            if s not in seen:
                seen.append(s)
            if len(seen) > max_distinct:
                break
        # Only useful when it's genuinely low-cardinality (an encoding, not free text).
        return f"{table}.{name}", (seen if 0 < len(seen) <= max_distinct else None)

    out: dict[str, list[str]] = {}
    workers = min(4, len(candidates))  # modest — don't spray connections at the DB
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, seen in pool.map(_probe, candidates):  # map preserves candidate order
            if seen is not None and len(out) < max_columns:
                out[key] = seen
    return out


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
        if "." in key:
            names.append(key.split(".", 1)[1])
        else:
            names.append(key)
    return names


def _find_schema_columns(orchestrator: AskOrchestrator, table: str, database: str) -> list[ColumnInfo] | None:
    key = _schema_key(database, table)
    if key in orchestrator.run_state.schemas:
        return orchestrator.run_state.schemas[key]
    for schema_key, columns in orchestrator.run_state.schemas.items():
        if schema_key == table or schema_key.endswith(f".{table}"):
            return columns
    return None


def _expand_to_full_columns(
    orchestrator: AskOrchestrator,
    disclosed: list[tuple[str, str, list[ColumnInfo]]],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    """Return the disclosed tables with their FULL column lists. The resolved schema
    carries only the minimal-necessary columns; clarification needs to see every
    column of the relevant tables so it grounds 'which column?' questions in the real
    fields instead of guessing. Falls back to the given columns if a describe fails."""
    full: list[tuple[str, str, list[ColumnInfo]]] = []
    for db, table, columns in disclosed:
        cols = _find_schema_columns(orchestrator, table, db)
        if not cols:
            try:
                cols = orchestrator.schema.describe_table(table, database=db)
                _remember_table_schema(orchestrator, table, db, cols)
            except Exception:  # noqa: BLE001 — grounding is best-effort, never fatal
                cols = None
        # Prefer whichever list has more columns (the full one), never fewer.
        if cols and len(cols) >= len(columns):
            full.append((db, table, cols))
        else:
            full.append((db, table, columns))
    return full


def _collect_disclosed_schemas(
    orchestrator: AskOrchestrator,
    args: dict[str, Any],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    database_default = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
    tables_arg = args.get("tables")
    selected: list[tuple[str, str, list[ColumnInfo]]] = []

    if isinstance(tables_arg, list) and tables_arg:
        for raw in tables_arg:
            name = str(raw).strip()
            if not name:
                continue
            db = database_default
            columns = _find_schema_columns(orchestrator, name, db)
            if columns is None:
                columns = orchestrator.schema.describe_table(name, database=db)
                _remember_table_schema(orchestrator, name, db, columns)
            selected.append((db, name, columns))
        return selected

    if orchestrator.run_state.schemas:
        for key, columns in orchestrator.run_state.schemas.items():
            db = orchestrator.run_state.schema_db.get(key, database_default)
            table = key.split(".", 1)[1] if "." in key else key
            selected.append((db, table, columns))
        return selected

    table = str(args.get("table") or orchestrator.run_state.table or "").strip()
    if not table:
        return []
    db = str(args.get("database") or orchestrator.run_state.table_database or database_default)
    columns = _find_schema_columns(orchestrator, table, db)
    if columns is None:
        columns = orchestrator.schema.describe_table(table, database=db)
        _remember_table_schema(orchestrator, table, db, columns)
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

