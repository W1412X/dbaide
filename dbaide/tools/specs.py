"""Tool specifications for DBAide tool registry."""
from __future__ import annotations

from typing import Any


class ToolSpec:
    """Tool specification with schema, permissions, and timeout."""

    __slots__ = (
        "name", "description", "input_schema", "output_schema",
        "permission_level", "timeout_seconds", "max_rows",
        "cache_policy", "safe_for_auto_call",
    )

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        permission_level: str = "safe_metadata",
        timeout_seconds: int = 30,
        max_rows: int | None = None,
        cache_policy: str = "none",
        safe_for_auto_call: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}
        self.permission_level = permission_level
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.cache_policy = cache_policy
        self.safe_for_auto_call = safe_for_auto_call

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permission_level": self.permission_level,
            "timeout_seconds": self.timeout_seconds,
            "max_rows": self.max_rows,
            "cache_policy": self.cache_policy,
            "safe_for_auto_call": self.safe_for_auto_call,
        }


# Permission levels
SAFE_METADATA = "safe_metadata"
SAFE_PROFILE = "safe_profile"
SQL_VALIDATE = "sql_validate"
SQL_EXECUTE = "sql_execute"
CONFIG_WRITE = "config_write"


# Pre-defined tool specs (registered tools only; see agent/toolkit.py)
LIST_DATABASES = ToolSpec(
    name="list_databases",
    description="List all databases in a connection",
    input_schema={},
    output_schema={"databases": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

LIST_TABLES = ToolSpec(
    name="list_tables",
    description="List all tables in a database",
    input_schema={"database": "string"},
    output_schema={"tables": "list[TableInfo]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

DESCRIBE_TABLE = ToolSpec(
    name="describe_table",
    description="Get full table metadata: columns, indexes, declared FKs and available asset row/sample metadata",
    input_schema={"table": "string", "database": "string"},
    output_schema={
        "columns": "list[ColumnInfo]",
        "indexes": "list[dict]",
        "foreign_keys": "list[dict]",
        "row_count": "integer",
        "sample_rows": "list[dict]",
        "object_notes": "list[dict]",
        "disclosed_tables": "list[string]",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

INSPECT_METADATA = ToolSpec(
    name="inspect_metadata",
    description=(
        "Inspect database metadata without running business-data SQL: exact table/column "
        "existence, column existence across tables, or indexes/FKs for selected tables."
    ),
    input_schema={
        "database": "string",
        "tables": "list[string]",
        "table_name": "string",
        "column_name": "string",
        "include_columns": "boolean",
        "include_indexes": "boolean",
        "include_foreign_keys": "boolean",
        "limit": "integer",
    },
    output_schema={
        "database": "string",
        "tables": "list[dict]",
        "matched_columns": "list[dict]",
        "disclosed_tables": "list[string]",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=20,
    safe_for_auto_call=True,
)

RETRIEVE_JOIN_CONTEXT = ToolSpec(
    name="retrieve_join_context",
    description=(
        "Retrieve join evidence for selected tables without deciding the final join: "
        "user-saved joins and declared FKs by default; set infer_semantic/validate_sample "
        "for semantic inference or sample validation."
    ),
    input_schema={
        "request": "string",
        "tables": "list[string]",
        "database": "string",
        "infer_semantic": "boolean",
        "validate_sample": "boolean",
        "sample_size": "integer",
    },
    output_schema={
        "report_id": "string",
        "tables": "list[string]",
        "relations": "list[dict]",
        "source_summary": "string",
        "warnings": "list[string]",
    },
    permission_level=SAFE_PROFILE,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

VALIDATE_JOINS = ToolSpec(
    name="validate_joins",
    description=(
        "Refresh sample evidence and confidence scores for join relations "
        "(type alignment and match rate adjust ranking; does not hard-reject odd business joins)."
    ),
    input_schema={"sample_size": "integer"},
    output_schema={"relations": "list[dict]", "count": "integer", "validated_count": "integer"},
    permission_level=SAFE_PROFILE,
    timeout_seconds=45,
    safe_for_auto_call=True,
)

LIST_JOINS = ToolSpec(
    name="list_joins",
    description=(
        "List saved join catalog for this connection. Filter by tables, min_confidence, "
        "or exact endpoint (table, column, ref_table, ref_column)."
    ),
    input_schema={
        "database": "string",
        "tables": "list[string]",
        "min_confidence": "number",
        "table": "string",
        "column": "string",
        "ref_table": "string",
        "ref_column": "string",
    },
    output_schema={"joins": "list[dict]", "count": "integer"},
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

ADD_JOIN = ToolSpec(
    name="add_join",
    description=(
        "Add or upsert a join in the catalog. source=user sets confidence 0.99; "
        "source=agent for agent-pinned candidates."
    ),
    input_schema={
        "table": "string",
        "column": "string",
        "ref_table": "string",
        "ref_column": "string",
        "database": "string",
        "source": "string",
        "join_type": "string",
        "reason": "string",
        "confidence": "number",
    },
    output_schema={"join": "dict", "relation": "dict"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

UPDATE_JOIN = ToolSpec(
    name="update_join",
    description="Update a saved join by id (endpoints, join_type, reason; user joins keep confidence 0.99).",
    input_schema={
        "id": "string",
        "database": "string",
        "table": "string",
        "column": "string",
        "ref_table": "string",
        "ref_column": "string",
        "join_type": "string",
        "reason": "string",
        "confidence": "number",
    },
    output_schema={"join": "dict", "relation": "dict"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

DELETE_JOIN = ToolSpec(
    name="delete_join",
    description="Delete a saved join by id or by full endpoint.",
    input_schema={
        "id": "string",
        "database": "string",
        "table": "string",
        "column": "string",
        "ref_table": "string",
        "ref_column": "string",
    },
    output_schema={"deleted": "boolean"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

ANNOTATE_OBJECT = ToolSpec(
    name="annotate_object",
    description=(
        "Save a durable, AUTHORITATIVE user note on a database/table/column (e.g. a "
        "column's timezone, what a status value means, or that a table is deprecated and "
        "which one replaces it). Upserts by object. ONLY call this when the user has "
        "STATED or CONFIRMED the fact — never invent one. Set scope=column with table+column, "
        "scope=table with table, or scope=database. Notes persist across sessions and are "
        "shown to the agent at high priority on future questions."
    ),
    input_schema={
        "scope": "string",
        "note": "string",
        "database": "string",
        "table": "string",
        "column": "string",
    },
    output_schema={"annotation": "dict"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

VALIDATE_SQL = ToolSpec(
    name="validate_sql",
    description="Validate SQL for safety and correctness",
    input_schema={"sql": "string"},
    output_schema={"ok": "boolean", "issues": "list[string]", "normalized_sql": "string"},
    permission_level=SQL_VALIDATE,
    timeout_seconds=5,
    safe_for_auto_call=True,
)

EXPLAIN_SQL = ToolSpec(
    name="explain_sql",
    description="Run EXPLAIN on a SQL query",
    input_schema={"sql": "string", "database": "string"},
    output_schema={"plan": "list[dict]"},
    permission_level=SQL_VALIDATE,
    timeout_seconds=15,
    safe_for_auto_call=True,
)

EXECUTE_READONLY_SQL = ToolSpec(
    name="execute_readonly_sql",
    description=(
        "Execute a validated read-only SQL query as exploratory/intermediate evidence. "
        "The agent loop continues after success; use execute_sql for the final answer query."
    ),
    input_schema={
        "sql": "string",
        "database": "string",
        "purpose": "string",
        "save_as": "string",
        "limit": "integer",
        "timeout_seconds": "integer",
    },
    output_schema={"artifact_id": "string", "rows": "list[dict]", "columns": "list[string]", "row_count": "integer"},
    permission_level=SQL_EXECUTE,
    timeout_seconds=30,
    max_rows=10000,
    safe_for_auto_call=False,
)

EXECUTE_SQL = ToolSpec(
    name="execute_sql",
    description="Run the final validated read-only SQL answer query. The agent loop may finish after success.",
    input_schema={
        "sql": "string",
        "database": "string",
        "purpose": "string",
        "save_as": "string",
        "limit": "integer",
        "timeout_seconds": "integer",
    },
    output_schema={"artifact_id": "string", "rows": "list[dict]", "columns": "list[string]", "row_count": "integer"},
    permission_level=SQL_EXECUTE,
    timeout_seconds=30,
    max_rows=10000,
    safe_for_auto_call=False,
)

PROFILE_TABLE = ToolSpec(
    name="profile_table",
    description="Profile columns of a table",
    input_schema={"table": "string", "columns": "list[string]", "database": "string"},
    output_schema={"profiles": "list[ColumnProfile]"},
    permission_level=SAFE_PROFILE,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

COLUMN_STATS = ToolSpec(
    name="column_stats",
    description=(
        "On-demand, type-aware statistics overview for one or more columns. Pick the "
        "metrics that matter for the question; omit `metrics` to get sensible defaults "
        "per type. Candidates: numeric/temporal → min,max,null_rate(,distinct_count); "
        "string → min_len,max_len,null_rate,empty_rate(,distinct_count); "
        "categorical/boolean → distinct_count,null_rate(,top_values)."
    ),
    input_schema={"table": "string", "columns": "list[string]", "metrics": "list[string]", "database": "string"},
    output_schema={"columns": "list[dict]"},
    permission_level=SAFE_PROFILE,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

ASK_USER = ToolSpec(
    name="ask_user",
    description=(
        "Pause the run and ask the user to settle an irreducible business choice "
        "(per the clarification rule); provide concrete options. Pauses until the user replies."
    ),
    input_schema={"question": "string", "options": "list[string]"},
    output_schema={"pending": "boolean", "question": "string", "options": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=300,
    safe_for_auto_call=True,
)

RETRIEVE_MEMORY_ITEM = ToolSpec(
    name="retrieve_memory_item",
    description=(
        "Fetch archived raw evidence by ref (e.g. mem:3, w2, schema:1, sql:1) when a "
        "compressed memory summary is not enough."
    ),
    input_schema={"ref": "string"},
    output_schema={
        "id": "string",
        "action": "string",
        "summary": "string",
        "source_refs": "list[string]",
        "payload": "dict",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=5,
    safe_for_auto_call=True,
)

DISCOVER_SCHEMA = ToolSpec(
    name="discover_schema",
    description="Progressive LLM schema discovery (instance → database → table → column)",
    input_schema={"question": "string"},
    output_schema={"hits": "list[dict]", "trace": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

RETRIEVE_SCHEMA_CONTEXT = ToolSpec(
    name="retrieve_schema_context",
    description=(
        "Retrieve schema evidence for the question without deciding the final schema: "
        "candidate tables, columns, authoritative user notes, inactive/missing paths. "
        "Does not fetch join relations, profile/sample rows, or validate relationships."
    ),
    input_schema={
        "request": "string",
        "database": "string",
        "focus_terms": "list[string]",
        "need": "string",
        "limit": "integer",
        "scope": "dict",
    },
    output_schema={
        "report_id": "string",
        "candidates": "list[dict]",
        "missing": "list[string]",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=90,
    safe_for_auto_call=True,
)

GENERATE_SQL = ToolSpec(
    name="generate_sql",
    description="Generate read-only SQL using disclosed table column metadata (all described/retrieved tables, or explicit tables arg)",
    input_schema={"question": "string", "table": "string", "tables": "list[string]", "database": "string"},
    output_schema={"sql": "string", "rationale": "string", "confidence": "float", "tables": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=30,
    safe_for_auto_call=True,
)
