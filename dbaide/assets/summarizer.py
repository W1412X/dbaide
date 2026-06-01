from __future__ import annotations

from typing import Any

from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, IndexInfo, TableInfo

# v4: lean, type-aware docs — column docs carry index detail + minimal type-aware
# stats (no per-column LLM summaries); table docs carry the raw DDL + index list
# and drop per-column descriptions (those live in the column docs).
ASSET_SCHEMA_VERSION = 4

_MAX_DESC = 200  # descriptions stay short — the agent explores details via SQL


def _to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__slots__"):
        return {slot: getattr(obj, slot) for slot in obj.__slots__}
    if hasattr(obj, "__dict__"):
        return obj.__dict__.copy()
    return {}


def _short(text: str | None) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _MAX_DESC else text[: _MAX_DESC - 1] + "…"


class AssetSummarizer:
    """Build lean offline asset documents from catalog facts (DDL, indexes, FKs,
    type-aware min/max) plus short table/db/instance descriptions. Deliberately
    minimal: the agent can always explore finer detail by running SQL."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def column_doc(
        self,
        *,
        instance: str,
        database: str,
        table: str,
        column: ColumnInfo,
        profile: ColumnProfile | None,
        indexes: list[IndexInfo] | None = None,
    ) -> dict[str, Any]:
        col_indexes = [ix.to_dict() for ix in (indexes or []) if column.name in ix.columns]
        return {
            "kind": "column",
            "asset_schema_version": ASSET_SCHEMA_VERSION,
            "instance": instance,
            "database": database,
            "table": table,
            "column": column.name,
            "name": column.name,
            "data_type": column.data_type,
            "nullable": column.nullable,
            "default": column.default,
            "primary_key": column.primary_key,
            "indexed": bool(col_indexes) or column.indexed,
            "indexes": col_indexes,            # which indexes (incl. composite) cover this column
            "source_comment": column.comment,  # DB-provided comment, if any — the description
            "profile_status": "profiled" if profile else "not_profiled",
            "statistics": lean_column_statistics(profile),  # type-aware, minimal
        }

    def table_doc(
        self,
        *,
        instance: str,
        database: str,
        table: TableInfo,
        columns: list[dict[str, Any]],
        foreign_keys: list[ForeignKeyInfo],
        indexes: list[IndexInfo] | None = None,
        ddl: str = "",
    ) -> dict[str, Any]:
        description = self._llm_table_summary(database, table, columns, foreign_keys) or factual_table_summary(table, columns)
        return {
            "kind": "table",
            "asset_schema_version": ASSET_SCHEMA_VERSION,
            "instance": instance,
            "database": database,
            "table": table.name,
            "name": table.name,
            "table_type": table.table_type,
            "estimated_rows": table.estimated_rows,
            "source_comment": table.comment,
            "description": _short(description),
            "ddl": ddl,                        # raw CREATE TABLE — the authoritative shape
            # Lean column list: identity only. Descriptions live in the column docs.
            "columns": [
                {
                    "name": col.get("name"),
                    "data_type": col.get("data_type"),
                    "primary_key": col.get("primary_key"),
                    "nullable": col.get("nullable"),
                }
                for col in columns
            ],
            "indexes": [ix.to_dict() for ix in (indexes or [])],
            "foreign_keys": [foreign_key_record(fk) for fk in foreign_keys],
        }

    def database_doc(self, *, instance: str, database: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
        description = self._llm_database_summary(database, tables) or factual_database_summary(database, tables)
        return {
            "kind": "database",
            "asset_schema_version": ASSET_SCHEMA_VERSION,
            "instance": instance,
            "database": database,
            "name": database,
            "description": _short(description),
            "table_count": len(tables),
            "tables": [
                {"name": table.get("name"), "description": _short(table.get("description"))}
                for table in tables
            ],
        }

    def instance_doc(self, *, instance: str, databases: list[dict[str, Any]]) -> dict[str, Any]:
        description = self._llm_instance_summary(instance, databases) or factual_instance_summary(instance, databases)
        return {
            "kind": "instance",
            "asset_schema_version": ASSET_SCHEMA_VERSION,
            "instance": instance,
            "name": instance,
            "description": _short(description),
            "databases": [
                {
                    "name": db.get("name"),
                    "description": _short(db.get("description")),
                    "table_count": len(db.get("tables") or []),
                }
                for db in databases
            ],
        }

    def _llm_table_summary(
        self,
        database: str,
        table: TableInfo,
        columns: list[dict[str, Any]],
        foreign_keys: list[ForeignKeyInfo],
    ) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            col_names = ", ".join(str(c.get("name")) for c in columns[:30])
            payload = self.llm.complete_json(
                [
                    LLMMessage("system", "Summarize what a database table is for. Return JSON only."),
                    LLMMessage("user", f"Database: {database}\nTable: {table.name}\nComment: {table.comment}\nColumns: {col_names}"),
                ],
                schema_hint='Return {"summary": "ONE short sentence (<=20 words) on what this table holds"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_database_summary(self, database: str, tables: list[dict[str, Any]]) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            names = ", ".join(str(t.get("name")) for t in tables[:40])
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database's purpose. Return JSON only."),
                 LLMMessage("user", f"Database: {database}\nTables: {names}")],
                schema_hint='Return {"summary": "ONE short sentence on the database\'s domain"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_instance_summary(self, instance: str, databases: list[dict[str, Any]]) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            names = ", ".join(str(db.get("name")) for db in databases)
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database instance. Return JSON only."),
                 LLMMessage("user", f"Instance: {instance}\nDatabases: {names}")],
                schema_hint='Return {"summary": "ONE short sentence on this instance"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""


def lean_column_statistics(profile: ColumnProfile | None) -> dict[str, Any]:
    """Minimal, type-aware stats. Ordered types (numeric/temporal) get min/max;
    low-cardinality types get a distinct count; everything carries a null rate.
    Anything finer is left for the agent to query on demand."""
    if profile is None:
        return {}
    stats: dict[str, Any] = {"data_kind": profile.data_kind}
    if profile.null_rate is not None:
        stats["null_rate"] = round(float(profile.null_rate), 4)
    kind = profile.data_kind
    if kind in ("numeric", "temporal"):
        if profile.min_value is not None:
            stats["min_value"] = profile.min_value
        if profile.max_value is not None:
            stats["max_value"] = profile.max_value
    elif kind in ("categorical", "boolean"):
        if profile.distinct_count is not None:
            stats["distinct_count"] = profile.distinct_count
    return stats


def foreign_key_record(fk: ForeignKeyInfo) -> dict[str, Any]:
    return {**_to_dict(fk), "source": "foreign_key"}


def factual_table_summary(table: TableInfo, columns: list[dict[str, Any]]) -> str:
    if table.comment:
        return _short(table.comment)
    names = ", ".join(str(col.get("name")) for col in columns[:8])
    suffix = "…" if len(columns) > 8 else ""
    return _short(f"{table.name} ({len(columns)} cols: {names}{suffix})")


def factual_database_summary(database: str, tables: list[dict[str, Any]]) -> str:
    names = ", ".join(str(table.get("name")) for table in tables[:10])
    suffix = "…" if len(tables) > 10 else ""
    return _short(f"{database}: {len(tables)} tables ({names}{suffix})")


def factual_instance_summary(instance: str, databases: list[dict[str, Any]]) -> str:
    names = ", ".join(str(db.get("name")) for db in databases)
    return _short(f"{instance}: {len(databases)} database(s) ({names})")


# ── Markdown rendering (asset preview) ───────────────────────────────────────

def _index_label(idx: dict[str, Any]) -> str:
    cols = ", ".join(idx.get("columns") or [])
    flags = []
    if idx.get("primary"):
        flags.append("PK")
    if idx.get("unique"):
        flags.append("unique")
    kind = idx.get("type") or ""
    suffix = f" [{', '.join(flags)}]" if flags else ""
    type_suffix = f" · {kind}" if kind and not idx.get("primary") else ""
    return f"{idx.get('name', '')} ({cols}){suffix}{type_suffix}"


def render_instance_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Instance')}", ""]
    if doc.get("description"):
        lines += [doc["description"], ""]
    lines += ["## Databases", "", "| Database | Tables | Description |", "| --- | --- | --- |"]
    for db in doc.get("databases", []):
        lines.append(f"| {db.get('name', '')} | {db.get('table_count', '?')} | {db.get('description', '')} |")
    lines.append("")
    return "\n".join(lines)


def render_database_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Database')}", ""]
    if doc.get("description"):
        lines += [doc["description"], ""]
    lines += ["## Tables", "", "| Table | Description |", "| --- | --- |"]
    for table in doc.get("tables", []):
        lines.append(f"| {table.get('name', '')} | {table.get('description', '')} |")
    lines.append("")
    return "\n".join(lines)


def render_table_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Table')}", ""]
    lines += ["## Summary", ""]
    lines.append(f"- **Database:** {doc.get('database', '?')}")
    lines.append(f"- **Type:** {doc.get('table_type', 'table')}")
    if doc.get("estimated_rows") is not None:
        lines.append(f"- **Estimated rows:** {doc.get('estimated_rows', '?')}")
    if doc.get("source_comment"):
        lines.append(f"- **Comment:** {doc['source_comment']}")
    if doc.get("description"):
        lines.append(f"- **Description:** {doc['description']}")
    lines.append("")

    # Identity-only column list (descriptions live in the column docs).
    lines += ["## Columns", "", "| Column | Type | PK | Null |", "| --- | --- | --- | --- |"]
    for col in doc.get("columns", []):
        pk = "✓" if col.get("primary_key") else ""
        nullable = "" if col.get("nullable") is False else "✓"
        lines.append(f"| {col.get('name', '')} | {col.get('data_type', '')} | {pk} | {nullable} |")
    lines.append("")

    indexes = doc.get("indexes", [])
    if indexes:
        lines += ["## Indexes", ""]
        for idx in indexes:
            lines.append(f"- {_index_label(idx)}")
        lines.append("")

    fks = doc.get("foreign_keys", [])
    if fks:
        lines += ["## Foreign Keys", "", "| Column | References |", "| --- | --- |"]
        for fk in fks:
            lines.append(f"| {fk.get('column', '')} | {fk.get('ref_table', '')}.{fk.get('ref_column', '')} |")
        lines.append("")

    if doc.get("ddl"):
        lines += ["## DDL", "", "```sql", str(doc["ddl"]).strip(), "```", ""]

    return "\n".join(lines)


def render_column_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('table', '?')}.{doc.get('name', '?')}", ""]
    if doc.get("source_comment"):
        lines += ["## Meaning", "", doc["source_comment"], ""]

    lines += ["## Type And Constraints", ""]
    lines.append(f"- **Type:** {doc.get('data_type', '?')}")
    lines.append(f"- **Nullable:** {doc.get('nullable', '?')}")
    lines.append(f"- **Primary key:** {doc.get('primary_key', False)}")
    if doc.get("default") is not None:
        lines.append(f"- **Default:** {doc['default']}")
    lines.append("")

    indexes = doc.get("indexes", [])
    if indexes:
        lines += ["## Indexes", ""]
        for idx in indexes:
            lines.append(f"- {_index_label(idx)}")
        lines.append("")

    stats = doc.get("statistics", {})
    if stats:
        lines += ["## Stats", ""]
        if stats.get("data_kind"):
            lines.append(f"- **Kind:** {stats['data_kind']}")
        if stats.get("null_rate") is not None:
            lines.append(f"- **Null rate:** {stats['null_rate']:.2%}")
        if stats.get("distinct_count") is not None:
            lines.append(f"- **Distinct:** {stats['distinct_count']}")
        if stats.get("min_value") is not None:
            lines.append(f"- **Min:** {stats['min_value']}")
        if stats.get("max_value") is not None:
            lines.append(f"- **Max:** {stats['max_value']}")
        lines.append("")

    return "\n".join(lines)
