from __future__ import annotations

from typing import Any

from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, TableInfo

ASSET_SCHEMA_VERSION = 3


def _to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__slots__"):
        return {slot: getattr(obj, slot) for slot in obj.__slots__}
    if hasattr(obj, "__dict__"):
        return obj.__dict__.copy()
    return {}


class AssetSummarizer:
    """Build offline asset documents from catalog facts and optional LLM summaries."""

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
    ) -> dict[str, Any]:
        summary = self._llm_column_summary(database, table, column, profile) or factual_column_summary(column, profile)
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
            "indexed": column.indexed,
            "source_comment": column.comment,
            "semantic_summary": summary,
            "profile_status": "profiled" if profile else "not_profiled",
            "statistics": build_column_statistics(profile),
            "sample_values": profile.sample_values[:50] if profile else [],
            "top_values": profile.top_values[:50] if profile else [],
        }

    def table_doc(
        self,
        *,
        instance: str,
        database: str,
        table: TableInfo,
        columns: list[dict[str, Any]],
        foreign_keys: list[ForeignKeyInfo],
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
            "description": description,
            "column_index": build_column_index(columns),
            "columns": [
                {
                    "name": col.get("name"),
                    "data_type": col.get("data_type"),
                    "primary_key": col.get("primary_key"),
                    "indexed": col.get("indexed"),
                    "source_comment": col.get("source_comment"),
                    "semantic_summary": col.get("semantic_summary"),
                }
                for col in columns
            ],
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
            "description": description,
            "table_count": len(tables),
            "tables": [
                {
                    "name": table.get("name"),
                    "description": table.get("description"),
                    "estimated_rows": table.get("estimated_rows"),
                }
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
            "description": description,
            "databases": [
                {
                    "name": db.get("name"),
                    "description": db.get("description"),
                    "table_count": len(db.get("tables") or []),
                }
                for db in databases
            ],
        }

    def _llm_column_summary(
        self,
        database: str,
        table: str,
        column: ColumnInfo,
        profile: ColumnProfile | None,
    ) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            payload = self.llm.complete_json(
                [
                    LLMMessage(
                        "system",
                        "Summarize a database column from catalog facts and profile statistics. Return JSON only.",
                    ),
                    LLMMessage(
                        "user",
                        f"Database: {database}\nTable: {table}\nColumn: {column}\nProfile: {profile}",
                    ),
                ],
                schema_hint='Return {"summary": "one concise summary of meaning and usage"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

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
            payload = self.llm.complete_json(
                [
                    LLMMessage("system", "Summarize a database table from column documents. Return JSON only."),
                    LLMMessage("user", f"Database: {database}\nTable: {table}\nColumns: {columns}\nForeign keys: {foreign_keys}"),
                ],
                schema_hint='Return {"summary": "one concise table description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_database_summary(self, database: str, tables: list[dict[str, Any]]) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database from table documents. Return JSON only."), LLMMessage("user", f"Database: {database}\nTables: {tables}")],
                schema_hint='Return {"summary": "one concise database description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_instance_summary(self, instance: str, databases: list[dict[str, Any]]) -> str:
        if isinstance(self.llm, NullLLMClient):
            return ""
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database instance from database documents. Return JSON only."), LLMMessage("user", f"Instance: {instance}\nDatabases: {databases}")],
                schema_hint='Return {"summary": "one concise instance description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""


def factual_column_summary(column: ColumnInfo, profile: ColumnProfile | None) -> str:
    parts = [f"{column.name}: {column.data_type or 'unknown'}"]
    if column.comment:
        parts.append(f"comment={column.comment}")
    flags = []
    if column.primary_key:
        flags.append("primary_key")
    if column.indexed:
        flags.append("indexed")
    if flags:
        parts.append(", ".join(flags))
    if profile:
        null_rate = f"{profile.null_rate:.2%}" if profile.null_rate is not None else "unknown"
        parts.append(
            f"rows={profile.row_count}, nulls={profile.null_count} ({null_rate}), "
            f"distinct={profile.distinct_count}"
        )
        if profile.top_values:
            top = ", ".join(str(x.get("value")) for x in profile.top_values[:5])
            parts.append(f"top_values={top}")
        if profile.min_value is not None or profile.max_value is not None:
            parts.append(f"range={profile.min_value}..{profile.max_value}")
    return ". ".join(parts)


def build_column_statistics(profile: ColumnProfile | None) -> dict[str, Any]:
    if profile is None:
        return {}
    return {
        "data_kind": profile.data_kind,
        "row_count": profile.row_count,
        "null_count": profile.null_count,
        "null_rate": profile.null_rate,
        "distinct_count": profile.distinct_count,
        "distinct_ratio": profile.distinct_ratio,
        "min_value": profile.min_value,
        "max_value": profile.max_value,
        "numeric_stats": profile.numeric_stats,
        "text_stats": profile.text_stats,
        "temporal_stats": profile.temporal_stats,
        "distribution": profile.distribution,
        "sample_rows": profile.sample_rows[:50],
    }


def build_column_index(columns: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "primary_key": [str(col.get("name")) for col in columns if col.get("primary_key")],
        "indexed": [str(col.get("name")) for col in columns if col.get("indexed")],
    }


def foreign_key_record(fk: ForeignKeyInfo) -> dict[str, Any]:
    return {**_to_dict(fk), "source": "foreign_key"}


def factual_table_summary(table: TableInfo, columns: list[dict[str, Any]]) -> str:
    names = ", ".join(str(col.get("name")) for col in columns[:12])
    suffix = f" (+{len(columns) - 12} more)" if len(columns) > 12 else ""
    text = f"Table {table.name}: {len(columns)} columns [{names}{suffix}]"
    if table.comment:
        text += f". Comment: {table.comment}"
    return text


def factual_database_summary(database: str, tables: list[dict[str, Any]]) -> str:
    names = ", ".join(str(table.get("name")) for table in tables[:12])
    return f"Database {database}: {len(tables)} tables [{names}]"


def factual_instance_summary(instance: str, databases: list[dict[str, Any]]) -> str:
    names = ", ".join(str(db.get("name")) for db in databases)
    return f"Instance {instance}: {len(databases)} database(s) [{names}]"


def render_instance_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Instance')}", ""]
    if doc.get("description"):
        lines.append(doc["description"])
        lines.append("")
    lines.append("## Databases")
    lines.append("")
    lines.append("| Database | Tables | Description |")
    lines.append("| --- | --- | --- |")
    for db in doc.get("databases", []):
        lines.append(f"| {db.get('name', '')} | {db.get('table_count', '?')} | {db.get('description', '')} |")
    lines.append("")
    lines.append(f"*Built at: {doc.get('built_at', 'unknown')}*")
    return "\n".join(lines)


def render_database_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Database')}", ""]
    if doc.get("description"):
        lines.append(doc["description"])
        lines.append("")
    lines.append("## Tables")
    lines.append("")
    lines.append("| Table | Rows | Description |")
    lines.append("| --- | --- | --- |")
    for table in doc.get("tables", []):
        rows = table.get("estimated_rows", "?")
        lines.append(f"| {table.get('name', '')} | {rows} | {table.get('description', '')} |")
    lines.append("")
    return "\n".join(lines)


def render_table_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Table')}", ""]
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Database:** {doc.get('database', '?')}")
    lines.append(f"- **Type:** {doc.get('table_type', 'table')}")
    lines.append(f"- **Estimated rows:** {doc.get('estimated_rows', '?')}")
    if doc.get("source_comment"):
        lines.append(f"- **Comment:** {doc['source_comment']}")
    if doc.get("description"):
        lines.append(f"- **Description:** {doc['description']}")
    lines.append("")

    lines.append("## Columns")
    lines.append("")
    lines.append("| Column | Type | PK | Indexed | Description |")
    lines.append("| --- | --- | --- | --- | --- |")
    for col in doc.get("columns", []):
        pk = "✓" if col.get("primary_key") else ""
        indexed = "✓" if col.get("indexed") else ""
        desc = col.get("semantic_summary") or col.get("source_comment") or ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(
            f"| {col.get('name', '')} | {col.get('data_type', '')} | {pk} | {indexed} | {desc} |"
        )
    lines.append("")

    fks = doc.get("foreign_keys", [])
    if fks:
        lines.append("## Foreign Keys")
        lines.append("")
        lines.append("| Column | References |")
        lines.append("| --- | --- |")
        for fk in fks:
            lines.append(f"| {fk.get('column', '')} | {fk.get('ref_table', '')}.{fk.get('ref_column', '')} |")
        lines.append("")

    column_index = doc.get("column_index", {})
    if column_index.get("primary_key") or column_index.get("indexed"):
        lines.append("## Column Index")
        lines.append("")
        if column_index.get("primary_key"):
            lines.append(f"- **Primary key:** {', '.join(column_index['primary_key'])}")
        if column_index.get("indexed"):
            lines.append(f"- **Indexed:** {', '.join(column_index['indexed'])}")
        lines.append("")

    return "\n".join(lines)


def render_column_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('table', '?')}.{doc.get('name', '?')}", ""]
    lines.append("## Meaning")
    lines.append("")
    if doc.get("semantic_summary"):
        lines.append(doc["semantic_summary"])
    elif doc.get("source_comment"):
        lines.append(doc["source_comment"])
    else:
        lines.append(f"{doc.get('name', '?')} is a {doc.get('data_type', 'unknown')} column.")
    lines.append("")

    lines.append("## Type And Constraints")
    lines.append("")
    lines.append(f"- **Type:** {doc.get('data_type', '?')}")
    lines.append(f"- **Nullable:** {doc.get('nullable', '?')}")
    lines.append(f"- **Primary key:** {doc.get('primary_key', False)}")
    lines.append(f"- **Indexed:** {doc.get('indexed', False)}")
    if doc.get("default"):
        lines.append(f"- **Default:** {doc['default']}")
    lines.append("")

    stats = doc.get("statistics", {})
    if stats:
        lines.append("## Profile")
        lines.append("")
        lines.append(f"- **Row count:** {stats.get('row_count', '?')}")
        lines.append(f"- **Null count:** {stats.get('null_count', '?')}")
        null_rate = stats.get("null_rate")
        if null_rate is not None:
            lines.append(f"- **Null rate:** {null_rate:.2%}")
        lines.append(f"- **Distinct count:** {stats.get('distinct_count', '?')}")
        distinct_ratio = stats.get("distinct_ratio")
        if distinct_ratio is not None:
            lines.append(f"- **Distinct ratio:** {distinct_ratio:.2%}")
        if stats.get("data_kind"):
            lines.append(f"- **Data kind:** {stats['data_kind']}")
        if stats.get("min_value") is not None:
            lines.append(f"- **Min:** {stats['min_value']}")
        if stats.get("max_value") is not None:
            lines.append(f"- **Max:** {stats['max_value']}")
        lines.append("")

    samples = doc.get("sample_values", [])
    if samples:
        lines.append("## Sample Values")
        lines.append("")
        for v in samples[:10]:
            lines.append(f"- `{v}`")
        if len(samples) > 10:
            lines.append(f"- ... ({len(samples)} total)")
        lines.append("")

    top = doc.get("top_values", [])
    if top:
        lines.append("## Top Values")
        lines.append("")
        lines.append("| Value | Count |")
        lines.append("| --- | --- |")
        for item in top[:10]:
            lines.append(f"| {item.get('value', '')} | {item.get('count', '')} |")
        lines.append("")

    return "\n".join(lines)
