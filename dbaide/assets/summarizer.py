from __future__ import annotations

from typing import Any

from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo, ForeignKeyInfo, IndexInfo, TableInfo

# v5: the TABLE is the leaf of disclosure (instance → database → table). There are
# no per-column documents anymore — the table doc carries the full DDL-as-JSON
# (columns + indexes + FKs), a row-count, and a truncated, type-safe sample. Any
# per-column statistics are fetched on demand via the column_stats agent tool.
ASSET_SCHEMA_VERSION = 5

_MAX_DESC = 200       # descriptions stay short — the agent explores details via SQL
_SAMPLE_ROWS = 5      # rows kept in the table sample
_SAMPLE_CELL_MAX = 120  # truncate long varchar/json/text cells in the sample


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

    def table_doc(
        self,
        *,
        instance: str,
        database: str,
        table: TableInfo,
        columns: list[ColumnInfo],
        foreign_keys: list[ForeignKeyInfo],
        indexes: list[IndexInfo] | None = None,
        ddl: str = "",
        row_count: int | None = None,
        sample_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        col_dicts = [
            {
                "name": col.name,
                "data_type": col.data_type,
                "nullable": col.nullable,
                "primary_key": col.primary_key,
                "default": col.default,
                "comment": col.comment,        # DB-provided comment, if any
            }
            for col in columns
        ]
        description = self._llm_table_summary(database, table, col_dicts, foreign_keys) or factual_table_summary(table, col_dicts)
        return {
            "kind": "table",
            "asset_schema_version": ASSET_SCHEMA_VERSION,
            "instance": instance,
            "database": database,
            "table": table.name,
            "name": table.name,
            "table_type": table.table_type,
            "row_count": row_count if row_count is not None else table.estimated_rows,
            "row_count_exact": row_count is not None,
            "source_comment": table.comment,
            "description": _short(description),
            "ddl": ddl,                        # raw CREATE TABLE — the authoritative shape
            # Full structured shape (DDL-as-JSON). The table is the leaf of disclosure;
            # the agent fetches per-column stats on demand via the column_stats tool.
            "columns": col_dicts,
            "indexes": [ix.to_dict() for ix in (indexes or [])],
            "foreign_keys": [foreign_key_record(fk) for fk in foreign_keys],
            "sample_rows": truncate_sample_rows(sample_rows or []),
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


def truncate_cell(value: Any) -> Any:
    """Make a sampled cell safe to store/render: long varchar/json/text are clipped,
    binary becomes a marker. Works on the Python value so it's DB-type agnostic."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary {len(bytes(value))} bytes>"
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= _SAMPLE_CELL_MAX else text[: _SAMPLE_CELL_MAX - 1] + "…"


def truncate_sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: truncate_cell(val) for key, val in row.items()}
        for row in (rows or [])[:_SAMPLE_ROWS]
    ]


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


def _md_cell(text: Any, limit: int = 200) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")[:limit]


def render_instance_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Instance')}", ""]
    if doc.get("description"):
        lines += [doc["description"], ""]
    if doc.get("user_note"):
        lines += [f"> 📝 **User note:** {doc['user_note']}", ""]
    lines += ["## Databases", "", "| Database | Tables | Description | User note |",
              "| --- | --- | --- | --- |"]
    for db in doc.get("databases", []):
        lines.append(
            f"| {db.get('name', '')} | {db.get('table_count', '?')} "
            f"| {_md_cell(db.get('description'))} | {_md_cell(db.get('user_note'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_database_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Database')}", ""]
    if doc.get("description"):
        lines += [doc["description"], ""]
    if doc.get("user_note"):
        lines += [f"> 📝 **User note:** {doc['user_note']}", ""]
    lines += ["## Tables", "", "| Table | Description | User note |", "| --- | --- | --- |"]
    for table in doc.get("tables", []):
        lines.append(
            f"| {table.get('name', '')} | {_md_cell(table.get('description'))} "
            f"| {_md_cell(table.get('user_note'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_table_markdown(doc: dict[str, Any]) -> str:
    lines = [f"# {doc.get('name', 'Table')}", ""]
    if doc.get("user_note"):
        lines += [f"> 📝 **User note:** {doc['user_note']}", ""]
    lines += ["## Summary", ""]
    lines.append(f"- **Database:** {doc.get('database', '?')}")
    lines.append(f"- **Type:** {doc.get('table_type', 'table')}")
    if doc.get("row_count") is not None:
        label = "Rows" if doc.get("row_count_exact") else "Rows (est.)"
        lines.append(f"- **{label}:** {doc.get('row_count')}")
    if doc.get("source_comment"):
        lines.append(f"- **Comment:** {doc['source_comment']}")
    if doc.get("description"):
        lines.append(f"- **Description:** {doc['description']}")
    lines.append("")

    # Full structured column shape (the table is the disclosure leaf). The User note
    # column carries the user's authoritative annotation (stored separately from the
    # asset, merged in for display).
    lines += ["## Columns", "", "| Column | Type | PK | Null | Comment | User note |",
              "| --- | --- | --- | --- | --- | --- |"]
    for col in doc.get("columns", []):
        pk = "✓" if col.get("primary_key") else ""
        nullable = "" if col.get("nullable") is False else "✓"
        comment = _md_cell(col.get("comment"), 60)
        user_note = _md_cell(col.get("user_note"), 120)
        lines.append(
            f"| {col.get('name', '')} | {col.get('data_type', '')} | {pk} | {nullable} "
            f"| {comment} | {user_note} |"
        )
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

    sample = doc.get("sample_rows") or []
    cols = [c.get("name") for c in doc.get("columns", [])]
    if sample and cols:
        lines += ["## Sample", "", "| " + " | ".join(str(c) for c in cols) + " |",
                  "| " + " | ".join("---" for _ in cols) + " |"]
        for row in sample:
            cells = [str(row.get(c, "")).replace("|", "\\|") for c in cols]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    if doc.get("ddl"):
        lines += ["## DDL", "", "```sql", str(doc["ddl"]).strip(), "```", ""]

    return "\n".join(lines)
