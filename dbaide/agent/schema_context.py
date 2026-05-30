"""Shared schema disclosure helpers for tool loop and staged fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator
    from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit

MAX_DISCLOSED_TABLES = 4


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


def collect_relations(orchestrator: AskOrchestrator, tables: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Collect unique FK records for the given tables."""
    relations: list[dict[str, Any]] = []
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
            relations.append(fk)
    return relations


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
