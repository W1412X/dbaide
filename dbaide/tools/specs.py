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
    description="Get column metadata for a table (accumulates in loop context for multi-table SQL)",
    input_schema={"table": "string", "database": "string"},
    output_schema={"columns": "list[ColumnInfo]", "disclosed_tables": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

GET_RELATIONS = ToolSpec(
    name="get_relations",
    description=(
        "Join hints for disclosed tables: declared FKs plus LLM semantic inference when needed. "
        "Returns confidence-ranked edges with optional sample evidence."
    ),
    input_schema={"tables": "list[string]", "table": "string", "database": "string", "sample_size": "integer"},
    output_schema={"relations": "list[dict]", "count": "integer", "validated_count": "integer"},
    permission_level=SAFE_PROFILE,
    timeout_seconds=30,
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
    description="Execute a validated read-only SQL query",
    input_schema={"sql": "string", "database": "string", "limit": "integer", "timeout_seconds": "integer"},
    output_schema={"rows": "list[dict]", "columns": "list[string]", "row_count": "integer"},
    permission_level=SQL_EXECUTE,
    timeout_seconds=30,
    max_rows=10000,
    safe_for_auto_call=False,
)

EXECUTE_SQL = ToolSpec(
    name="execute_sql",
    description="Alias for execute_readonly_sql — run validated read-only SQL",
    input_schema={"sql": "string", "database": "string"},
    output_schema={"rows": "list[dict]", "columns": "list[string]", "row_count": "integer"},
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

ASK_USER = ToolSpec(
    name="ask_user",
    description="Ask the user a clarification question",
    input_schema={"question": "string", "options": "list[string]"},
    output_schema={"answer": "string"},
    permission_level=SAFE_METADATA,
    timeout_seconds=300,
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

GENERATE_SQL = ToolSpec(
    name="generate_sql",
    description="Generate read-only SQL using disclosed table column metadata (all describe_table results, or tables arg)",
    input_schema={"question": "string", "table": "string", "tables": "list[string]", "database": "string"},
    output_schema={"sql": "string", "rationale": "string", "confidence": "float", "tables": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=30,
    safe_for_auto_call=True,
)

SYNTHESIZE_SCHEMA_ANSWER = ToolSpec(
    name="synthesize_schema_answer",
    description="Write a markdown answer from progressive schema discovery",
    input_schema={"question": "string"},
    output_schema={"answer": "string"},
    permission_level=SAFE_METADATA,
    timeout_seconds=30,
    safe_for_auto_call=True,
)
