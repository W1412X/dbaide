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
    input_schema={"database": {"type": "string"}},
    output_schema={"tables": "list[TableInfo]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

DESCRIBE_TABLE = ToolSpec(
    name="describe_table",
    description="Get full table metadata: columns, indexes, declared FKs and available asset row/sample metadata",
    input_schema={
        "table": {"type": "string", "required": True},
        "database": {"type": "string"},
    },
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
        "Inspect database metadata without running business-data SQL. Use for "
        "schema/system catalog questions: exact table/column existence, "
        "column existence across tables, indexes/FKs. "
        "Whole-database scan returns up to `limit` tables (default 256); "
        "when total_tables exceeds that, pass table_name/tables to reach the rest."
    ),
    input_schema={
        "database": {"type": "string"},
        "tables": {"type": "list[string]", "description": "filter to these tables"},
        "table_name": {"type": "string", "description": "filter to one table by name"},
        "column_name": {"type": "string", "description": "find this column across tables"},
        "include_columns": {"type": "boolean"},
        "include_indexes": {"type": "boolean"},
        "include_foreign_keys": {"type": "boolean"},
        "limit": {"type": "integer", "description": "max tables to scan (default 256)"},
    },
    output_schema={
        "database": "string",
        "tables": "list[dict]",
        "matched_columns": "list[dict]",
        "disclosed_tables": "list[string]",
        "total_tables": "integer",
        "more_tables": "boolean",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=20,
    safe_for_auto_call=True,
)

RETRIEVE_JOIN_CONTEXT = ToolSpec(
    name="retrieve_join_context",
    description=(
        "Retrieve join evidence for selected tables without deciding the final join. "
        "By default reads only user-saved join catalog entries and declared foreign keys. "
        "Set infer_semantic=true and/or validate_sample=true only when the main LLM explicitly "
        "needs that extra evidence. Use after the main LLM "
        "has narrowed candidate tables and needs relation evidence for SQL planning."
    ),
    input_schema={
        "request": {"type": "string"},
        "tables": {"type": "list[string]", "description": "the candidate tables to get join evidence for"},
        "database": {"type": "string"},
        "infer_semantic": {"type": "boolean", "description": "enable LLM semantic inference (default false)"},
        "validate_sample": {"type": "boolean", "description": "run sample match-rate check (default false)"},
        "sample_size": {"type": "integer"},
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
    input_schema={"sample_size": {"type": "integer"}},
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
        "database": {"type": "string"},
        "tables": {"type": "list[string]", "description": "filter by table names"},
        "min_confidence": {"type": "number"},
        "table": {"type": "string", "description": "exact endpoint filter"},
        "column": {"type": "string"},
        "ref_table": {"type": "string"},
        "ref_column": {"type": "string"},
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
        "table": {"type": "string", "required": True},
        "column": {"type": "string", "required": True},
        "ref_table": {"type": "string", "required": True},
        "ref_column": {"type": "string", "required": True},
        "database": {"type": "string"},
        "source": {"type": "string", "description": "user|agent (default user)"},
        "join_type": {"type": "string", "description": "LEFT|INNER|etc."},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
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
        "id": {"type": "string", "required": True, "description": "join id from list_joins"},
        "database": {"type": "string"},
        "table": {"type": "string"},
        "column": {"type": "string"},
        "ref_table": {"type": "string"},
        "ref_column": {"type": "string"},
        "join_type": {"type": "string", "description": "LEFT|INNER|etc."},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
    output_schema={"join": "dict", "relation": "dict"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

DELETE_JOIN = ToolSpec(
    name="delete_join",
    description="Delete a saved join by id or by full endpoint (table+column+ref_table+ref_column).",
    input_schema={
        "id": {"type": "string", "description": "join id; OR pass full endpoint below"},
        "database": {"type": "string"},
        "table": {"type": "string"},
        "column": {"type": "string"},
        "ref_table": {"type": "string"},
        "ref_column": {"type": "string"},
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
        "note": {"type": "string", "required": True},
        "scope": {"type": "string", "description": "column|table|database; inferred from other params if omitted"},
        "database": {"type": "string"},
        "table": {"type": "string"},
        "column": {"type": "string"},
    },
    output_schema={"annotation": "dict"},
    permission_level=CONFIG_WRITE,
    timeout_seconds=10,
    safe_for_auto_call=True,
)

VALIDATE_SQL = ToolSpec(
    name="validate_sql",
    description="Validate SQL for safety and correctness",
    input_schema={
        "sql": {"type": "string", "description": "defaults to current draft from generate_sql"},
    },
    output_schema={
        "ok": "boolean",
        "normalized_sql": "string",
        "issues": "list[dict]",
        "risk_level": "string",
        "warnings": "list[string]",
        "requires_confirmation": "boolean",
    },
    permission_level=SQL_VALIDATE,
    timeout_seconds=5,
    safe_for_auto_call=True,
)

EXPLAIN_SQL = ToolSpec(
    name="explain_sql",
    description="Run EXPLAIN on a SQL query",
    input_schema={
        "sql": {"type": "string", "description": "defaults to current draft"},
        "database": {"type": "string"},
    },
    output_schema={"ok": "boolean", "explain": "list[dict]", "issues": "list[string]"},
    permission_level=SQL_VALIDATE,
    timeout_seconds=15,
    safe_for_auto_call=True,
)

EXECUTE_SQL = ToolSpec(
    name="execute_sql",
    description=(
        "Execute a validated SQL query and record it in this run's SQL history. "
        "Pass purpose (≤20 chars, user language). "
        "Set exploratory=true for intermediate evidence-gathering queries "
        "(loop continues, does NOT update the run's final query_result); "
        "omit or false when the result IS the answer (updates query_result). "
        "Returns first 20 rows; truncated=true means more rows exist."
    ),
    input_schema={
        "sql": {"type": "string", "description": "defaults to current draft from generate_sql"},
        "database": {"type": "string", "description": "defaults to working database"},
        "purpose": {"type": "string", "description": "short label ≤20 chars, user language"},
        "save_as": {"type": "string", "description": "artifact name for later reference"},
        "limit": {"type": "integer", "description": "row cap; defaults to session limit"},
        "timeout_seconds": {"type": "integer"},
        "exploratory": {"type": "boolean", "default": False,
                        "description": "true for intermediate checks that don't set the final result"},
    },
    output_schema={
        "artifact_id": "string",
        "columns": "list[string]",
        "rows": "list[dict]",
        "row_count": "integer",
        "truncated": "boolean",
        "elapsed_ms": "number",
        "purpose": "string",
        "result_summary": "string",
        "sql": "string",
        "database": "string",
    },
    permission_level=SQL_EXECUTE,
    timeout_seconds=30,
    max_rows=10000,
    safe_for_auto_call=False,
)

PROFILE_TABLE = ToolSpec(
    name="profile_table",
    description=(
        "Profile columns of a table. Pass `columns` to profile exactly those. If you "
        "omit `columns`, it profiles a WINDOW of the table's columns (default the first "
        "`column_limit`, from `column_offset`) — it does NOT profile every column, and "
        "the un-profiled ones are not computed. "
        "The result reports `total_columns`; when total_columns exceeds the "
        "window, page with column_offset=<next index> or name the columns explicitly so "
        "no column is silently skipped."
    ),
    input_schema={
        "table": {"type": "string", "required": True},
        "columns": {"type": "list[string]", "description": "profile exactly these; omit to profile a window"},
        "database": {"type": "string"},
        "column_offset": {"type": "integer", "description": "start index for windowed profiling"},
        "column_limit": {"type": "integer"},
    },
    output_schema={
        "profiles": "list[ColumnProfile]",
        "total_columns": "integer",
        "column_offset": "integer",
        "more_columns": "boolean",
    },
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
        "categorical/boolean → distinct_count,null_rate(,top_values). Omitting `columns` "
        "covers EVERY column in one scan. top_values returns the `top_k` most frequent "
        "values (default 10); distinct_count is the true total — raise `top_k` (or GROUP BY "
        "in SQL) when the value you need isn't among the most frequent."
    ),
    input_schema={
        "table": {"type": "string", "required": True},
        "columns": {"type": "list[string]", "description": "omit to cover every column"},
        "metrics": {"type": "list[string]", "description": "min,max,null_rate,distinct_count,top_values,etc.; omit for defaults"},
        "database": {"type": "string"},
        "top_k": {"type": "integer", "description": "how many top values to return (default 10)"},
    },
    output_schema={"columns": "list[dict]"},
    permission_level=SAFE_PROFILE,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

ASK_USER = ToolSpec(
    name="ask_user",
    description=(
        "Ask the user a clarification question only for irreducible business intent. "
        "Do not use for evidence the tools can inspect first: table/column existence, "
        "field source, joins/FKs, indexes, row samples, value distributions, SQL "
        "feasibility, or timezone/date conversion implied by schema/user notes. "
        "Gather evidence with schema/profile/SQL tools first, then ask only the "
        "remaining business choice with concrete options."
    ),
    input_schema={
        "question": {"type": "string", "required": True},
        "options": {"type": "list[string]", "description": "concrete choices for the user"},
    },
    output_schema={"pending": "boolean", "question": "string", "options": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=300,
    safe_for_auto_call=True,
)

RUN_SUBAGENT = ToolSpec(
    name="run_subagent",
    description=(
        "Delegate a bounded, independent database subtask to a child DBAide agent "
        "with the same connection, schema scope, confirmed criteria, and read-only "
        "tooling. Use for separable research/verification work whose result the main "
        "agent will incorporate, not for simple one-step tool calls. The child may "
        "inspect schema, generate/execute read-only SQL if execute=true, and returns a "
        "compact answer, SQL, warnings, and a row-preserving preview."
    ),
    input_schema={
        "task": {"type": "string", "required": True, "description": "specific subtask for the child agent"},
        "context": {"type": "string", "description": "extra constraints/evidence to include"},
        "database": {"type": "string", "description": "defaults to current working database"},
        "execute": {"type": "boolean", "default": True, "description": "allow child read-only SQL execution"},
        "max_steps": {"type": "integer", "description": "child step budget, capped by the parent session"},
    },
    output_schema={
        "task": "string",
        "status": "string",
        "answer": "string",
        "sql": "string",
        "result_preview": "list[dict]",
        "row_preview": "dict",
        "warnings": "list[string]",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=120,
    safe_for_auto_call=True,
)

RENDER_CHART = ToolSpec(
    name="render_chart",
    description=(
        "Render an interactive chart from tabular SQL results. Call when the user asks for a "
        "chart/visualization (图表/可视化) or when a chart clarifies comparisons or trends. "
        "Requires aggregated numeric data — run execute_sql first. Pass artifact_id from that "
        "result, inline data, or use the latest query result. A dedicated chart agent picks the "
        "chart type and field mapping. For complex analytical requests, call this tool multiple "
        "times, once per coherent view; do not force different units or business meanings into "
        "one chart. In finish, embed each chart inline with "
        "`{{chart:N}}` (copy embed_markdown from tool output) at the appropriate position in your markdown answer."
    ),
    input_schema={
        "artifact_id": {"type": "string", "description": "from execute_sql result; or omit to use latest result"},
        "data": {"type": "list[dict]", "description": "inline rows (alternative to artifact_id)"},
        "intent": {"type": "string", "description": "describe the single view this chart should show"},
    },
    output_schema={
        "chart_id": "string",
        "chart_type": "string",
        "title": "string",
        "row_count": "integer",
        "preview": "string",
        "embed_markdown": "string",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

RETRIEVE_TURN = ToolSpec(
    name="retrieve_turn",
    description=(
        "Fetch the full content of one earlier turn in this chat session by turn_id "
        "(t1, t2, …). [Prior turns in this session] shows only Q/A/SQL summaries; "
        "call this when you need the user's exact clarifications, the full SQL history, the "
        "full answer, disclosed tables, verified facts, or ruled-out paths from that turn. "
        "`include` selects which fields to return (clarifications/sql/answer/tables/memory); omit it for all. "
        "When include contains sql, returns selected_sql (last query) and executed_sqls "
        "(all auto-executed queries with purpose tags)."
    ),
    input_schema={
        "turn_id": {"type": "string", "required": True, "description": "t1, t2, …"},
        "include": {"type": "list[string]", "description": "clarifications/sql/answer/tables/memory; omit for all"},
    },
    output_schema={
        "turn_id": "string",
        "question": "string",
        "status": "string",
        "clarifications": "list[string]",
        "selected_sql": "string",
        "executed_sqls": "list[object]",
        "answer_markdown": "string",
        "disclosed_tables": "list[string]",
        "verified_facts": "list[string]",
        "excluded_paths": "list[object]",
        "created_at": "number",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=5,
    safe_for_auto_call=True,
)

LIST_EARLIER_TURNS = ToolSpec(
    name="list_earlier_turns",
    description=(
        "Page earlier turns of this chat session (turns BEFORE the default window). "
        "Returns each turn's id (t1, t2, …) + question + 1-line answer summary, so "
        "you can spot a relevant earlier turn and then retrieve_turn(turn_id) for its "
        "details. `offset` counts from the oldest (offset=0 → start); `limit` defaults to 5."
    ),
    input_schema={
        "offset": {"type": "integer", "description": "counts from oldest (0 = start)"},
        "limit": {"type": "integer", "description": "default 5"},
    },
    output_schema={
        "turns": "list[dict]",
        "total": "integer",
        "more": "boolean",
    },
    permission_level=SAFE_METADATA,
    timeout_seconds=5,
    safe_for_auto_call=True,
)

DISCOVER_SCHEMA = ToolSpec(
    name="discover_schema",
    description="Progressive LLM schema discovery (instance → database → table → column)",
    input_schema={"question": {"type": "string"}},
    output_schema={"hits": "list[dict]", "trace": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=60,
    safe_for_auto_call=True,
)

RETRIEVE_SCHEMA_CONTEXT = ToolSpec(
    name="retrieve_schema_context",
    description=(
        "Retrieve schema evidence for the current question without deciding the final schema. "
        "Returns candidate tables, columns, authoritative user notes, inactive/missing "
        "paths, and missing information. It does NOT retrieve join relations; "
        "it also does not profile/sample rows or validate relationships. "
        "call retrieve_join_context after the main LLM decides relation evidence is needed. Use this as the default "
        "schema evidence tool for data questions; the main LLM must decide what to do next. "
        "Returns up to `limit` candidate tables (default 64), ranked by relevance; raise `limit` "
        "for a very broad question that may span more tables."
    ),
    input_schema={
        "request": {"type": "string", "description": "what schema evidence is needed"},
        "database": {"type": "string"},
        "focus_terms": {"type": "list[string]"},
        "need": {"type": "string"},
        "limit": {"type": "integer", "description": "max candidate tables (default 64)"},
        "scope": {"type": "dict"},
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
    input_schema={
        "question": {"type": "string", "description": "defaults to the user's question"},
        "table": {"type": "string", "description": "single target table"},
        "tables": {"type": "list[string]", "description": "multiple target tables"},
        "database": {"type": "string"},
    },
    output_schema={"sql": "string", "rationale": "string", "confidence": "float", "tables": "list[string]"},
    permission_level=SAFE_METADATA,
    timeout_seconds=30,
    safe_for_auto_call=True,
)
