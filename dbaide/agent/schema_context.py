"""Shared schema disclosure helpers for tool loop and staged fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import logging

from dbaide.llm import NullLLMClient
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator
    from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit

logger = logging.getLogger("dbaide.schema_context")

MAX_DISCLOSED_TABLES = 4


def normalize_db_table(table: str, database: str = "") -> tuple[str, str]:
    """Split a db-qualified table name into (database, table).

    The schema linker and the model often hand back a display name like
    ``platform.sys_user`` in the *table* field with an empty database — describing a
    table literally named "platform.sys_user" then finds nothing. When the table
    carries a dot and no explicit database is given, treat the prefix as the database.
    Quotes/backticks are stripped. Returns (database, table)."""
    def _clean(s: str) -> str:
        return str(s or "").strip().strip('`"[]').strip()

    table = _clean(table)
    database = _clean(database)
    if not database and "." in table:
        prefix, rest = table.split(".", 1)
        prefix, rest = _clean(prefix), _clean(rest)
        if prefix and rest:
            return prefix, rest
    return database, table


def table_targets_from_hits(
    hits: list[SchemaHit],
    active_database: str,
    *,
    limit: int = MAX_DISCLOSED_TABLES,
) -> list[tuple[str, str]]:
    """Return (database, table) pairs from discovery table hits."""
    seen: set[tuple[str, str]] = set()
    targets: list[tuple[str, str]] = []
    for hit in hits:
        if hit.kind != "table" or not hit.table:
            continue
        database = hit.database or active_database
        key = (database, hit.table)
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)
        if len(targets) >= limit:
            break
    return targets


def table_targets_from_discovery(
    discovery: DiscoveryResult,
    active_database: str,
    *,
    limit: int = MAX_DISCLOSED_TABLES,
) -> list[tuple[str, str]]:
    return table_targets_from_hits(discovery.hits, active_database, limit=limit)


def foreign_keys_for_table(orchestrator: AskOrchestrator, database: str, table: str) -> list[dict[str, Any]]:
    """Load declared foreign keys from offline assets or live catalog."""
    if database:
        doc = orchestrator.asset_store.table_doc(orchestrator.instance, database, table)
        if doc and doc.get("foreign_keys"):
            return [dict(item) for item in doc["foreign_keys"]]
    fks = orchestrator.schema.foreign_keys(table, database=database)
    return [
        {
            "table": fk.table,
            "column": fk.column,
            "ref_table": fk.ref_table,
            "ref_column": fk.ref_column,
            "source": "foreign_key",
        }
        for fk in fks
    ]


def collect_relations(
    orchestrator: AskOrchestrator,
    tables: list[tuple[str, str]],
    *,
    question: str = "",
    disclosed_schemas: list[tuple[str, str, list[ColumnInfo]]] | None = None,
    infer_semantic: bool = True,
    validate_sample: bool = True,
    sample_size: int = 150,
    parent: str = "",
) -> list[dict[str, Any]]:
    """Catalog (user/agent) → FK → LLM semantic; sample evidence adjusts confidence."""
    from dbaide.joins.catalog import merge_relation_layers
    from dbaide.agent.join_inference import tables_fully_connected

    schemas = disclosed_schemas or _disclosed_schemas_for_tables(orchestrator, tables)
    table_names = {table for _, table in tables}
    active_db = ""
    if tables:
        active_db = tables[0][0] or getattr(orchestrator, "_loop_database", "") or ""

    catalog_relations: list[dict[str, Any]] = []
    catalog = getattr(orchestrator, "join_catalog", None)
    if catalog is not None and len(table_names) >= 1:
        catalog_relations = catalog.relations_for_tables(
            orchestrator.instance,
            tables,
            database=active_db,
        )

    declared: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for database, table in tables:
        for fk in foreign_keys_for_table(orchestrator, database, table):
            key = (
                str(fk.get("table") or table),
                str(fk.get("column") or ""),
                str(fk.get("ref_table") or ""),
                str(fk.get("ref_column") or ""),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            declared.append(fk)

    semantic: list[dict[str, Any]] = []
    combined_for_connectivity = merge_relation_layers(catalog_relations, declared)
    need_semantic = (
        len(table_names) >= 2
        and infer_semantic
        and not isinstance(orchestrator.llm, NullLLMClient)
        and not tables_fully_connected(combined_for_connectivity, table_names)
        and len(schemas) >= 2
    )
    if need_semantic:
        from dbaide.agent.join_inference import SemanticJoinInferencer, merge_relation_lists

        try:
            inferencer = SemanticJoinInferencer(orchestrator.llm, orchestrator.asset_store, orchestrator.instance)
            semantic = inferencer.infer(
                question or getattr(orchestrator, "_loop_question", "") or "",
                schemas,
                declared=combined_for_connectivity,
                progress=orchestrator.progress,
                parent=parent,
            )
            semantic = merge_relation_lists([], semantic)
        except Exception as exc:
            logger.warning("semantic_join_inference_failed: %s", exc)

    relations = merge_relation_layers(catalog_relations, declared, semantic)

    if validate_sample and relations and schemas:
        from dbaide.agent.join_validation import validate_join_relations

        relations = validate_join_relations(
            orchestrator,
            relations,
            schemas,
            sample_size=sample_size,
            progress=orchestrator.progress,
            parent=parent,
            drop_invalid_semantic=True,
        )
        # Re-apply catalog user joins at full confidence after validation reorder.
        if catalog_relations:
            user_edges = [r for r in catalog_relations if str(r.get("source") or "") == "user"]
            if user_edges:
                relations = merge_relation_layers(user_edges, relations)

    return relations


def disclosed_schemas_for_tables(
    orchestrator: AskOrchestrator,
    tables: list[tuple[str, str]],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    return _disclosed_schemas_for_tables(orchestrator, tables)


def _disclosed_schemas_for_tables(
    orchestrator: AskOrchestrator,
    tables: list[tuple[str, str]],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    schemas: list[tuple[str, str, list[ColumnInfo]]] = []
    for database, table in tables:
        schema_key = f"{database}.{table}" if database else table
        columns = orchestrator._loop_schemas.get(schema_key)
        if columns is None:
            for key, cols in orchestrator._loop_schemas.items():
                if key == table or key.endswith(f".{table}"):
                    columns = cols
                    database = orchestrator._loop_schema_db.get(key, database)
                    break
        if columns is None:
            columns = orchestrator.schema.describe_table(table, database=database)
        schemas.append((database, table, columns))
    return schemas


def disclosed_table_keys(orchestrator: AskOrchestrator) -> list[tuple[str, str]]:
    """(database, table) pairs already described in the tool loop."""
    keys: list[tuple[str, str]] = []
    for schema_key in orchestrator._loop_schemas:
        db = str(orchestrator._loop_schema_db.get(schema_key) or orchestrator._loop_database or "")
        table = schema_key.split(".", 1)[1] if "." in schema_key else schema_key
        keys.append((db, table))
    return keys


def merge_sql_context(base: dict[str, Any], relations: list[dict[str, Any]]) -> dict[str, Any]:
    ctx = dict(base)
    if relations:
        ctx["foreign_keys"] = relations
    return ctx


def validation_feedback(issues: list[str]) -> str:
    text = "; ".join(issues) or "SQL validation failed"
    lowered = text.lower()
    if "unknown table" in lowered or "unknown column" in lowered:
        text += " Hint: call describe_table for missing objects before generate_sql."
    return text


def join_confidence_for_sql(relations: list[dict[str, Any]], sql: str) -> float:
    """Minimum confidence among join edges relevant to SQL (soft signal for risk gate)."""
    if not relations:
        return 1.0
    sql_lower = sql.lower()
    matched: list[dict[str, Any]] = []
    for rel in relations:
        left = str(rel.get("table") or "").strip().lower()
        right = str(rel.get("ref_table") or "").strip().lower()
        if left and right and left in sql_lower and right in sql_lower:
            matched.append(rel)
    pool = matched or relations
    return min(float(rel.get("confidence") or 0.0) for rel in pool)
