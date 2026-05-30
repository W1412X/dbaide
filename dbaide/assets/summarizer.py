from __future__ import annotations

from typing import Any

from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, TableInfo


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert an object with __slots__ to a dictionary."""
    if hasattr(obj, '__slots__'):
        return {slot: getattr(obj, slot) for slot in obj.__slots__}
    elif hasattr(obj, '__dict__'):
        return obj.__dict__.copy()
    else:
        return {}


class AssetSummarizer:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def column_doc(self, *, instance: str, database: str, table: str, column: ColumnInfo, profile: ColumnProfile | None) -> dict[str, Any]:
        role = infer_column_role(column, profile)
        summary = self._llm_column_summary(database, table, column, profile, role) or heuristic_column_summary(column, profile, role)
        semantics = infer_semantics(column, profile, role)
        return {
            "kind": "column",
            "asset_schema_version": 2,
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
            "likely_role": role,
            "semantic_tags": semantics["tags"],
            "quality": semantics["quality"],
            "usage_hints": semantics["usage_hints"],
            "semantic_summary": summary,
            "profile_status": "profiled" if profile else "not_profiled",
            "profile": _to_dict(profile) if profile else {},
            "statistics": build_column_statistics(profile),
            "sample_values": profile.sample_values[:50] if profile else [],
            "top_values": profile.top_values[:50] if profile else [],
        }

    def table_doc(self, *, instance: str, database: str, table: TableInfo, columns: list[dict[str, Any]], foreign_keys: list[ForeignKeyInfo]) -> dict[str, Any]:
        description = self._llm_table_summary(database, table, columns, foreign_keys) or heuristic_table_summary(table, columns)
        return {
            "kind": "table",
            "asset_schema_version": 2,
            "instance": instance,
            "database": database,
            "table": table.name,
            "name": table.name,
            "table_type": table.table_type,
            "estimated_rows": table.estimated_rows,
            "source_comment": table.comment,
            "description": description,
            "role_index": build_role_index(columns),
            "join_hints": build_join_hints(columns, foreign_keys),
            "columns": [
                {
                    "name": col.get("name"),
                    "data_type": col.get("data_type"),
                    "likely_role": col.get("likely_role"),
                    "semantic_tags": col.get("semantic_tags") or [],
                    "semantic_summary": col.get("semantic_summary"),
                }
                for col in columns
            ],
            "foreign_keys": [_to_dict(fk) for fk in foreign_keys],
        }

    def database_doc(self, *, instance: str, database: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
        description = self._llm_database_summary(database, tables) or heuristic_database_summary(database, tables)
        return {
            "kind": "database",
            "asset_schema_version": 2,
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
        description = self._llm_instance_summary(instance, databases) or heuristic_instance_summary(instance, databases)
        return {
            "kind": "instance",
            "asset_schema_version": 2,
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

    def _llm_column_summary(self, database: str, table: str, column: ColumnInfo, profile: ColumnProfile | None, role: str) -> str:
        try:
            payload = self.llm.complete_json(
                [
                    LLMMessage("system", "Summarize a database column for later schema linking. Return JSON only."),
                    LLMMessage(
                        "user",
                        f"Database: {database}\nTable: {table}\nColumn: {column}\nRole guess: {role}\nProfile: {profile}",
                    ),
                ],
                schema_hint='Return {"summary": "one concise Chinese summary of meaning and usage"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_table_summary(self, database: str, table: TableInfo, columns: list[dict[str, Any]], foreign_keys: list[ForeignKeyInfo]) -> str:
        try:
            payload = self.llm.complete_json(
                [
                    LLMMessage("system", "Summarize a database table from column documents. Return JSON only."),
                    LLMMessage("user", f"Database: {database}\nTable: {table}\nColumns: {columns}\nForeign keys: {foreign_keys}"),
                ],
                schema_hint='Return {"summary": "one concise Chinese table description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_database_summary(self, database: str, tables: list[dict[str, Any]]) -> str:
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database from table documents. Return JSON only."), LLMMessage("user", f"Database: {database}\nTables: {tables}")],
                schema_hint='Return {"summary": "one concise Chinese database description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""

    def _llm_instance_summary(self, instance: str, databases: list[dict[str, Any]]) -> str:
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", "Summarize a database instance from database documents. Return JSON only."), LLMMessage("user", f"Instance: {instance}\nDatabases: {databases}")],
                schema_hint='Return {"summary": "one concise Chinese instance description"}.',
            )
            return str(payload.get("summary") or "").strip()
        except Exception:
            return ""


def infer_column_role(column: ColumnInfo, profile: ColumnProfile | None) -> str:
    name = column.name.lower()
    typ = column.data_type.lower()
    if column.primary_key or name == "id" or name.endswith("_id"):
        return "identifier"
    time_name = (
        name in {"date", "day", "time", "timestamp"}
        or name.endswith("_at")
        or name.endswith("_time")
        or name.endswith("_date")
        or "created" in name
        or "updated" in name
    )
    if time_name or any(k in typ for k in ["date", "time"]):
        return "time"
    if any(k in name for k in ["amount", "price", "total", "fee", "cost", "money", "balance"]):
        return "measure_amount"
    if any(k in name for k in ["status", "state", "type", "category", "kind", "level"]):
        return "categorical_status"
    if profile and profile.data_kind == "boolean":
        return "boolean_flag"
    if profile and profile.data_kind == "temporal":
        return "time"
    if profile and profile.distinct_count is not None and profile.row_count and profile.distinct_count <= min(30, max(3, profile.row_count // 10)):
        return "categorical"
    if any(k in typ for k in ["int", "real", "numeric", "decimal", "float", "double"]):
        return "numeric"
    if any(k in typ for k in ["char", "text", "json"]):
        return "text"
    return "unknown"


def heuristic_column_summary(column: ColumnInfo, profile: ColumnProfile | None, role: str) -> str:
    parts = [f"{column.name} is {column.data_type or 'unknown'} type"]
    if column.comment:
        parts.append(f"Comment: {column.comment}")
    parts.append(f"Inferred role: {role}")
    if profile:
        null_rate = f"{profile.null_rate:.2%}" if profile.null_rate is not None else "unknown"
        distinct_ratio = f"{profile.distinct_ratio:.2%}" if profile.distinct_ratio is not None else "unknown"
        parts.append(f"Rows: {profile.row_count}, null: {profile.null_count} ({null_rate}), distinct: {profile.distinct_count} ({distinct_ratio})")
        if profile.top_values:
            top = ", ".join(str(x.get("value")) for x in profile.top_values[:5])
            parts.append(f"Top values: {top}")
        if profile.min_value is not None or profile.max_value is not None:
            parts.append(f"Range: {profile.min_value} to {profile.max_value}")
    return ". ".join(parts)


def infer_semantics(column: ColumnInfo, profile: ColumnProfile | None, role: str) -> dict[str, Any]:
    tags = [role]
    name = column.name.lower()
    if column.primary_key:
        tags.append("primary_key")
    if column.indexed:
        tags.append("indexed")
    if name.endswith("_id") and not column.primary_key:
        tags.append("foreign_key_candidate")
    if any(k in name for k in ["amount", "price", "total", "fee", "cost"]):
        tags.append("measure")
    if any(k in name for k in ["created", "updated", "date", "time"]) or role == "time":
        tags.append("time_filter_candidate")
    quality = {}
    usage_hints = []
    if profile:
        quality = {
            "row_count": profile.row_count,
            "null_rate": profile.null_rate,
            "distinct_ratio": profile.distinct_ratio,
            "has_profile": True,
        }
        if profile.null_rate is not None and profile.null_rate > 0.5:
            usage_hints.append("High null rate; use with caution as filter or join key.")
        if role in {"categorical", "categorical_status", "boolean_flag"}:
            usage_hints.append("Good for filtering, grouping, and enum value explanation.")
        if role in {"measure_amount", "numeric"}:
            usage_hints.append("Good for aggregation, sorting, and range filtering.")
        if role == "time":
            usage_hints.append("Good for time range filtering and time granularity aggregation.")
    else:
        quality = {"has_profile": False}
    return {"tags": list(dict.fromkeys(tags)), "quality": quality, "usage_hints": usage_hints}


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


def build_role_index(columns: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for col in columns:
        role = str(col.get("likely_role") or "unknown")
        index.setdefault(role, []).append(str(col.get("name")))
    return index


def build_join_hints(columns: list[dict[str, Any]], foreign_keys: list[ForeignKeyInfo]) -> list[dict[str, Any]]:
    hints = [_to_dict(fk) for fk in foreign_keys]
    existing = {(h.get("table"), h.get("column"), h.get("ref_table"), h.get("ref_column")) for h in hints}
    fk_columns = {h.get("column") for h in hints}
    for col in columns:
        name = str(col.get("name") or "")
        if name in fk_columns:
            continue
        if name.endswith("_id") and name != "id":
            guess = name[:-3]
            item = {"table": col.get("table"), "column": name, "ref_table": guess, "ref_column": "id", "source": "name_heuristic"}
            key = (item.get("table"), item.get("column"), item.get("ref_table"), item.get("ref_column"))
            if key not in existing:
                hints.append(item)
    return hints


def heuristic_table_summary(table: TableInfo, columns: list[dict[str, Any]]) -> str:
    roles = {}
    for col in columns:
        roles.setdefault(col.get("likely_role") or "unknown", 0)
        roles[col.get("likely_role") or "unknown"] += 1
    key_cols = ", ".join(str(col.get("name")) for col in columns[:8])
    return f"{table.name} has {len(columns)} columns. Key columns: {key_cols}. Role distribution: {roles}."


def heuristic_database_summary(database: str, tables: list[dict[str, Any]]) -> str:
    names = ", ".join(str(table.get("name")) for table in tables[:12])
    return f"{database} has {len(tables)} tables. Main tables: {names}."


def heuristic_instance_summary(instance: str, databases: list[dict[str, Any]]) -> str:
    names = ", ".join(str(db.get("name")) for db in databases)
    return f"{instance} has {len(databases)} databases: {names}."


# ─────────────────────────────────────────────────────────────────────────────
# Markdown asset generation
# ─────────────────────────────────────────────────────────────────────────────

def render_instance_markdown(doc: dict[str, Any]) -> str:
    """Render instance doc as Markdown."""
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
    """Render database doc as Markdown."""
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
    """Render table doc as Markdown."""
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

    # Columns
    lines.append("## Columns")
    lines.append("")
    lines.append("| Column | Type | Role | Nullable | Indexed | Description |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for col in doc.get("columns", []):
        nullable = "✓" if col.get("nullable") else ""
        indexed = "✓" if col.get("indexed") else ""
        desc = col.get("semantic_summary") or col.get("source_comment") or ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(f"| {col.get('name', '')} | {col.get('data_type', '')} | {col.get('likely_role', '')} | {nullable} | {indexed} | {desc} |")
    lines.append("")

    # Foreign Keys
    fks = doc.get("foreign_keys", [])
    if fks:
        lines.append("## Keys And Relations")
        lines.append("")
        lines.append("| Column | References | Source |")
        lines.append("| --- | --- | --- |")
        for fk in fks:
            source = fk.get("source", "foreign_key")
            lines.append(f"| {fk.get('column', '')} | {fk.get('ref_table', '')}.{fk.get('ref_column', '')} | {source} |")
        lines.append("")

    # Join Hints
    hints = doc.get("join_hints", [])
    if hints:
        lines.append("## Join Hints")
        lines.append("")
        for hint in hints:
            source = hint.get("source", "foreign_key")
            lines.append(f"- `{hint.get('column', '')}` → `{hint.get('ref_table', '')}.{hint.get('ref_column', '')}` ({source})")
        lines.append("")

    # Role Index
    role_index = doc.get("role_index", {})
    if role_index:
        lines.append("## Column Roles")
        lines.append("")
        for role, cols in role_index.items():
            lines.append(f"- **{role}**: {', '.join(cols)}")
        lines.append("")

    return "\n".join(lines)


def render_column_markdown(doc: dict[str, Any]) -> str:
    """Render column doc as Markdown."""
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

    # Type and constraints
    lines.append("## Type And Constraints")
    lines.append("")
    lines.append(f"- **Type:** {doc.get('data_type', '?')}")
    lines.append(f"- **Nullable:** {doc.get('nullable', '?')}")
    lines.append(f"- **Primary key:** {doc.get('primary_key', False)}")
    lines.append(f"- **Indexed:** {doc.get('indexed', False)}")
    if doc.get("default"):
        lines.append(f"- **Default:** {doc['default']}")
    lines.append("")

    # Profile
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
        if stats.get("min_value") is not None:
            lines.append(f"- **Min:** {stats['min_value']}")
        if stats.get("max_value") is not None:
            lines.append(f"- **Max:** {stats['max_value']}")
        lines.append("")

    # Sample values
    samples = doc.get("sample_values", [])
    if samples:
        lines.append("## Sample Values")
        lines.append("")
        for v in samples[:10]:
            lines.append(f"- `{v}`")
        if len(samples) > 10:
            lines.append(f"- ... ({len(samples)} total)")
        lines.append("")

    # Top values
    top = doc.get("top_values", [])
    if top:
        lines.append("## Top Values")
        lines.append("")
        lines.append("| Value | Count |")
        lines.append("| --- | --- |")
        for item in top[:10]:
            lines.append(f"| {item.get('value', '')} | {item.get('count', '')} |")
        lines.append("")

    # Usage hints
    hints = doc.get("usage_hints", [])
    if hints:
        lines.append("## Usage Hints")
        lines.append("")
        for hint in hints:
            lines.append(f"- {hint}")
        lines.append("")

    # Tags
    tags = doc.get("semantic_tags", [])
    if tags:
        lines.append(f"**Tags:** {', '.join(tags)}")
        lines.append("")

    return "\n".join(lines)

