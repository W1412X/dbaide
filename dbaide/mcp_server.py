"""MCP (Model Context Protocol) server exposing DBAide capabilities.

Speaks JSON-RPC 2.0 over stdio — compatible with Claude Code, Cursor, Windsurf,
Cline, Roo, and any other MCP-capable agent.

Start with::

    dbaide mcp                # default: expose all tools (ask + atomic tools)
    dbaide mcp --mode ask     # only the high-level ask tool
    dbaide mcp --mode tools   # only the atomic database tools

Two modes coexist:

    Mode A ("ask"): a single ``ask`` tool that runs the full DBAide agent pipeline
        — question in, answer out. The external agent treats DBAide as a database expert.

    Mode B ("tools"): atomic database tools (list_databases, describe_table,
        execute_sql, …) exposed directly. The external agent drives its own reasoning
        and uses DBAide as a database toolkit.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from typing import Any

from dbaide.agent.toolkit.result_preview import bounded_json_text, preview_rows

logger = logging.getLogger("dbaide.mcp")

# ── Protocol constants ──────────────────────────────────────────────────────

JSONRPC = "2.0"
SERVER_NAME = "dbaide"
PROTOCOL_VERSION = "2024-11-05"
# Protocol revisions whose tools wire-format is compatible with our handlers
# (initialize / tools.list / tools.call / ping are unchanged across these).
# When a client requests one of these we echo it back so the client stays on
# its preferred revision; otherwise we fall back to PROTOCOL_VERSION.
_SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2024-11-05", "2025-03-26", "2025-06-18",
})


def _server_version() -> str:
    try:
        from dbaide import __version__
        return __version__
    except Exception:
        return "0.0.0"


# ── Mode A: the high-level "ask" tool ──────────────────────────────────────

ASK_TOOL = {
    "name": "ask",
    "description": (
        "Ask a natural-language question about the database. "
        "DBAide generates SQL, executes it, and returns a formatted answer with the query used. "
        "All operations are read-only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Natural language question about the database",
            },
            "conn": {
                "type": "string",
                "description": "Connection name (omit to use the default connection)",
            },
            "database": {
                "type": "string",
                "description": "Database/schema name (optional)",
            },
        },
        "required": ["question"],
    },
    "annotations": {"title": "Ask Database", "readOnlyHint": True, "openWorldHint": False},
}

# ── Mode B: atomic database tools ─────────────────────────────────────────

_RO = {"readOnlyHint": True, "openWorldHint": False}

_ATOMIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_connections",
        "description": (
            "List all configured database connections with name, type, host, and database. "
            "Call this first to discover available connections before using other tools."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {**_RO, "title": "List Connections"},
    },
    {
        "name": "list_databases",
        "description": "List all databases/schemas available in the connection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
        },
        "annotations": {**_RO, "title": "List Databases"},
    },
    {
        "name": "list_tables",
        "description": (
            "List all tables in a database. "
            "If database is omitted, lists tables in the connection's default database."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
                "database": {"type": "string", "description": "Database/schema name (omit for connection default)"},
            },
        },
        "annotations": {**_RO, "title": "List Tables"},
    },
    {
        "name": "describe_table",
        "description": (
            "Get full column metadata for a table: name, type, nullable, default, "
            "primary key, comment."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "database": {"type": "string", "description": "Database/schema name"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["table"],
        },
        "annotations": {**_RO, "title": "Describe Table"},
    },
    {
        "name": "inspect_metadata",
        "description": (
            "Inspect database metadata: table/column existence, indexes, foreign keys. "
            "Filter by table_name, column_name, or pass tables for a multi-table scan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "tables": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Filter to these tables",
                },
                "table_name": {"type": "string", "description": "Filter to one table by name"},
                "column_name": {"type": "string", "description": "Find this column across tables"},
                "include_columns": {"type": "boolean", "default": True},
                "include_indexes": {"type": "boolean", "default": False},
                "include_foreign_keys": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "description": "Max tables to scan (default 256)"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
        },
        "annotations": {**_RO, "title": "Inspect Metadata"},
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query and return the results as JSON (columns + rows). "
            "Only SELECT queries are allowed; INSERT/UPDATE/DELETE/DROP are rejected. "
            "All queries are validated for safety before execution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL SELECT query to execute"},
                "database": {"type": "string", "description": "Database/schema name"},
                "limit": {"type": "integer", "description": "Max rows to return (default 100)"},
                "timeout_seconds": {"type": "integer", "description": "Query timeout in seconds"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["sql"],
        },
        "annotations": {**_RO, "title": "Execute SQL"},
    },
    {
        "name": "validate_sql",
        "description": "Validate a SQL query for safety and correctness without executing it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL query to validate"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["sql"],
        },
        "annotations": {**_RO, "title": "Validate SQL"},
    },
    {
        "name": "explain_sql",
        "description": "Run EXPLAIN on a SQL query to show the execution plan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL query to explain"},
                "database": {"type": "string", "description": "Database/schema name"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["sql"],
        },
        "annotations": {**_RO, "title": "Explain SQL"},
    },
    {
        "name": "column_stats",
        "description": (
            "Get specific statistical metrics for selected columns: "
            "choose from min, max, null_rate, distinct_count, min_len, max_len, empty_rate, top_values. "
            "Use this when you need particular metrics for particular columns. "
            "For a full overview of all columns, use profile_table instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "columns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Columns to analyze (omit for all)",
                },
                "metrics": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Metrics to compute: min, max, null_rate, distinct_count, min_len, max_len, empty_rate, top_values",
                },
                "database": {"type": "string"},
                "top_k": {"type": "integer", "description": "Top-K values to return (default 10)"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["table"],
        },
        "annotations": {**_RO, "title": "Column Stats"},
    },
    {
        "name": "profile_table",
        "description": (
            "Get a comprehensive profile of all columns in a table at once: "
            "row count, null count, distinct count, min/max, top values, and data types. "
            "Use this for an overview. For targeted metrics on specific columns, use column_stats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "columns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Columns to profile (omit for all)",
                },
                "database": {"type": "string"},
                "top_k": {"type": "integer", "description": "Top-K values (default 10)"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["table"],
        },
        "annotations": {**_RO, "title": "Profile Table"},
    },
    {
        "name": "sample_rows",
        "description": "Return a sample of rows from a table to preview its data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "database": {"type": "string"},
                "limit": {"type": "integer", "description": "Number of rows (default 20)"},
                "conn": {"type": "string", "description": "Connection name (omit for default)"},
            },
            "required": ["table"],
        },
        "annotations": {**_RO, "title": "Sample Rows"},
    },
]


# ── JSON-RPC helpers ────────────────────────────────────────────────────────

def _ok(id: Any, result: Any) -> dict:
    return {"jsonrpc": JSONRPC, "id": id, "result": result}


def _error(id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC, "id": id, "error": err}


def _text_content(text: str, *, is_error: bool = False) -> dict:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _data_content(data: dict, *, text: str | None = None) -> dict:
    """Tool result carrying both a human-readable text block and machine-readable
    ``structuredContent`` (MCP 2025-06). Spec-aware clients get typed data without
    re-parsing the text; clients on older revisions ignore the extra field. Only
    use for already-bounded payloads (row-previewed query results)."""
    return {
        "content": [{"type": "text", "text": text if text is not None else bounded_json_text(data)}],
        "structuredContent": data,
    }


def _positive_int_arg(
    arguments: dict[str, Any],
    name: str,
    default: int,
    *,
    maximum: int,
) -> int:
    raw = arguments.get(name, default)
    if raw in (None, ""):
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return min(value, maximum)


# ── Serialization helpers ──────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    """Convert tool-layer objects to JSON-safe dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _serialize(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


# ── Lazy tool context ──────────────────────────────────────────────────────

class _ToolContext:
    """Lazily initializes DB adapter + tool instances for a given connection.

    Rebuilds when the on-disk config changes (detected via ConfigManager hash).
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, Any, Any, Any]] = {}
        self._config_hash: dict[str, str] = {}

    def get(self, conn_name: str | None) -> tuple[Any, Any, Any, Any]:
        """Return (adapter, schema_tools, query_tools, profile_tools) for a connection."""
        key = conn_name or ""
        current_hash = self._connection_hash(conn_name)
        if key not in self._cache or self._config_hash.get(key) != current_hash:
            self._cache[key] = self._build(conn_name)
            self._config_hash[key] = current_hash
        return self._cache[key]

    @staticmethod
    def _connection_hash(conn_name: str | None) -> str:
        try:
            from dbaide.config import ConfigManager
            conn = ConfigManager().get_connection(conn_name)
            import hashlib
            raw = f"{conn.name}|{conn.type}|{getattr(conn, 'host', '')}|{getattr(conn, 'port', '')}|{getattr(conn, 'path', '')}|{getattr(conn, 'database', '')}"
            return hashlib.md5(raw.encode()).hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _build(conn_name: str | None) -> tuple[Any, Any, Any, Any]:
        from dbaide.config import ConfigManager
        from dbaide.adapters import build_adapter
        from dbaide.assets import AssetStore
        from dbaide.context.disclosure import DisclosureContext
        from dbaide.tools import SchemaTools, QueryTools, ProfileTools

        cfg = ConfigManager()
        connection = cfg.get_connection(conn_name)
        policy = None
        try:
            policy = cfg.policy_for(connection)
        except Exception:
            pass
        adapter = build_adapter(connection, policy=policy, caller="mcp")
        context = DisclosureContext()
        assets = AssetStore()
        schema = SchemaTools(adapter, context, assets=assets)
        query = QueryTools(adapter, context)
        profile = ProfileTools(adapter, context, assets=assets)
        return adapter, schema, query, profile


_ctx = _ToolContext()


# ── Handlers: Mode A ───────────────────────────────────────────────────────

def handle_ask(arguments: dict, *, progress_token: Any = None,
               cancel_event: "threading.Event | None" = None) -> dict:
    question = str(arguments.get("question") or "").strip()
    if not question:
        return _text_content("Error: question is required", is_error=True)

    conn = str(arguments.get("conn") or "").strip() or None
    database = str(arguments.get("database") or "").strip()

    try:
        from dbaide.config import ConfigManager
        from dbaide.assets import AssetStore
        from dbaide.llm import build_llm_client
        from dbaide.core.workflow import WorkflowEngine
        from dbaide.core.result import WorkflowRequest
        from dbaide.core.cancellation import CancelledError

        cfg = ConfigManager()
        connection = cfg.get_connection(conn)
        llm = build_llm_client(cfg.model())
        store = AssetStore()

        # Forward agent progress events to the MCP client as notifications/progress
        # (only when the client supplied a progressToken). progress must be
        # monotonically increasing; total is omitted (the run length is unknown).
        counter = {"n": 0}

        def on_progress(ev: Any) -> None:
            if progress_token is None:
                return
            if isinstance(ev, dict) and ev.get("kind") == "answer_chunk":
                return  # token-by-token deltas would flood the channel
            counter["n"] += 1
            if isinstance(ev, dict):
                text = str(ev.get("title") or ev.get("detail") or ev.get("stage") or "working")
            else:
                text = str(ev)
            _send({
                "jsonrpc": JSONRPC,
                "method": "notifications/progress",
                "params": {
                    "progressToken": progress_token,
                    "progress": counter["n"],
                    "message": text[:200],
                },
            })

        def cancel_check() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("cancelled by client")

        result = WorkflowEngine(connection, llm=llm, asset_store=store, model_config=cfg.model()).run(
            WorkflowRequest(
                question=question,
                connection_name=connection.name,
                database_scope=[database] if database else [],
            ),
            progress=on_progress if progress_token is not None else None,
            cancel_check=cancel_check,
        )
        if getattr(result, "status", None) and result.status.value == "cancelled":
            return _text_content("Cancelled.", is_error=True)

        parts: list[str] = []
        answer = result.answer_markdown or result.answer_plaintext or ""
        if answer:
            parts.append(answer)
        if result.selected_sql:
            parts.append(f"\n```sql\n{result.selected_sql}\n```")
        if result.executed_sqls:
            for entry in result.executed_sqls:
                if isinstance(entry, dict):
                    sql = entry.get("sql", "")
                    purpose = entry.get("purpose", "")
                    r = entry.get("result", "")
                    if sql:
                        label = f" ({purpose})" if purpose else ""
                        parts.append(f"\n**Executed SQL{label}:**\n```sql\n{sql}\n```")
                        if r:
                            parts.append(f"Result: {str(r)[:500]}")
        if result.warnings:
            parts.append("\n**Warnings:** " + "; ".join(result.warnings))
        if getattr(result, "status", None) and result.status.value == "failed":
            parts.append("\n**Status:** failed")

        text = "\n".join(parts) if parts else "(no answer)"
        return _text_content(text)

    except Exception as exc:
        logger.exception("ask tool failed")
        return _text_content(f"Error: {exc}", is_error=True)


# ── Handlers: Mode B ───────────────────────────────────────────────────────

def handle_list_connections(arguments: dict) -> dict:
    try:
        from dbaide.config import ConfigManager
        cfg = ConfigManager()
        conns = cfg.connections()
        items = []
        for name, c in conns.items():
            items.append({
                "name": name,
                "type": c.type,
                "host": getattr(c, "host", ""),
                "database": getattr(c, "database", ""),
            })
        return _text_content(json.dumps(items, ensure_ascii=False, indent=2))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_list_databases(arguments: dict) -> dict:
    try:
        conn = arguments.get("conn") or None
        _, schema, _, _ = _ctx.get(conn)
        databases = schema.list_databases()
        return _text_content(json.dumps(databases, ensure_ascii=False))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_list_tables(arguments: dict) -> dict:
    try:
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        _, schema, _, _ = _ctx.get(conn)
        tables = schema.list_tables(database=database)
        items = [_serialize(t) for t in tables]
        return _text_content(json.dumps(items, ensure_ascii=False, indent=2))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_describe_table(arguments: dict) -> dict:
    try:
        table = str(arguments.get("table") or "").strip()
        if not table:
            return _text_content("Error: table is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        _, schema, _, _ = _ctx.get(conn)
        columns = schema.describe_table(table, database=database)
        items = [_serialize(c) for c in columns]
        return _text_content(json.dumps(items, ensure_ascii=False, indent=2))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_inspect_metadata(arguments: dict) -> dict:
    try:
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        adapter, schema, _, _ = _ctx.get(conn)

        table_name = str(arguments.get("table_name") or "").strip()
        column_name = str(arguments.get("column_name") or "").strip()
        tables_filter = arguments.get("tables") or []
        include_columns = arguments.get("include_columns", True)
        include_indexes = arguments.get("include_indexes", False)
        include_fks = arguments.get("include_foreign_keys", True)
        limit = _positive_int_arg(arguments, "limit", 256, maximum=2048)

        all_tables = schema.list_tables(database=database)

        if table_name:
            all_tables = [t for t in all_tables if t.name == table_name]
        elif tables_filter:
            names = set(tables_filter)
            all_tables = [t for t in all_tables if t.name in names]

        total_matching_tables = len(all_tables)
        scanned_tables = all_tables[:limit]

        result_tables = []
        matched_columns = []

        for t in scanned_tables:
            entry: dict[str, Any] = {"name": t.name, "schema": t.schema, "comment": t.comment}
            if t.estimated_rows is not None:
                entry["estimated_rows"] = t.estimated_rows

            if include_columns or column_name:
                cols = schema.describe_table(t.name, database=database)
                if column_name:
                    for c in cols:
                        if c.name == column_name:
                            matched_columns.append({"table": t.name, "column": _serialize(c)})
                if include_columns:
                    entry["columns"] = [_serialize(c) for c in cols]

            if include_fks:
                fks = schema.foreign_keys(t.name, database=database)
                entry["foreign_keys"] = [_serialize(fk) for fk in fks]

            if include_indexes:
                try:
                    indexes = adapter.indexes(t.name, database=database)
                    entry["indexes"] = [_serialize(idx) for idx in indexes]
                except Exception:
                    entry["indexes"] = []

            result_tables.append(entry)

        result: dict[str, Any] = {
            "database": database,
            "tables": result_tables,
            "table_count": len(result_tables),
            "total_tables": total_matching_tables,
            "more_tables": total_matching_tables > len(scanned_tables),
        }
        if total_matching_tables > len(scanned_tables):
            result["note"] = (
                f"Scanned {len(scanned_tables)} of {total_matching_tables} matching tables "
                f"(limit={limit}). Raise limit or pass table_name/tables for the rest."
            )
        if column_name:
            result["matched_columns"] = matched_columns
        return _text_content(bounded_json_text(result))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_execute_sql(arguments: dict) -> dict:
    try:
        sql = str(arguments.get("sql") or "").strip()
        if not sql:
            return _text_content("Error: sql is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        limit = _positive_int_arg(arguments, "limit", 100, maximum=1000)
        timeout = None
        if arguments.get("timeout_seconds") not in (None, ""):
            timeout = _positive_int_arg(arguments, "timeout_seconds", 30, maximum=600)

        _, _, query, _ = _ctx.get(conn)
        result = query.execute_sql(
            sql, database=database, limit=limit,
            timeout_seconds=timeout,
        )
        rows, preview_meta = preview_rows(
            list(result.rows or []),
            columns=list(result.columns or []),
            max_rows=min(limit, 50),
        )
        data = {
            "columns": result.columns,
            "rows": rows,
            "row_preview": preview_meta,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "elapsed_ms": round(result.elapsed_ms, 2),
            "sql": result.sql,
        }
        return _data_content(data)
    except (ValueError, PermissionError) as exc:
        return _text_content(f"Rejected: {exc}", is_error=True)
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_validate_sql(arguments: dict) -> dict:
    try:
        sql = str(arguments.get("sql") or "").strip()
        if not sql:
            return _text_content("Error: sql is required", is_error=True)
        conn = arguments.get("conn") or None
        _, _, query, _ = _ctx.get(conn)
        result = query.validate_sql(sql)
        data = {
            "ok": result.ok,
            "normalized_sql": result.normalized_sql,
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity} for i in result.issues],
        }
        return _text_content(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_explain_sql(arguments: dict) -> dict:
    try:
        sql = str(arguments.get("sql") or "").strip()
        if not sql:
            return _text_content("Error: sql is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        _, _, query, _ = _ctx.get(conn)
        result = query.explain_sql(sql, database=database)
        data = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "sql": result.sql,
        }
        return _data_content(data, text=json.dumps(data, ensure_ascii=False, default=str, indent=2))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_column_stats(arguments: dict) -> dict:
    try:
        table = str(arguments.get("table") or "").strip()
        if not table:
            return _text_content("Error: table is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        columns = arguments.get("columns") or None
        metrics = arguments.get("metrics") or None
        top_k = _positive_int_arg(arguments, "top_k", 10, maximum=100)

        _, _, _, profile = _ctx.get(conn)
        stats = profile.column_stats(table, columns, metrics=metrics, database=database, top_k=top_k)
        return _text_content(bounded_json_text(stats))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_profile_table(arguments: dict) -> dict:
    try:
        table = str(arguments.get("table") or "").strip()
        if not table:
            return _text_content("Error: table is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        columns = arguments.get("columns") or None
        top_k = _positive_int_arg(arguments, "top_k", 10, maximum=100)

        _, _, _, profile = _ctx.get(conn)
        profiles = profile.profile_table(table, columns, database=database, top_k=top_k)
        items = [_serialize(p) for p in profiles]
        return _text_content(bounded_json_text(items))
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


def handle_sample_rows(arguments: dict) -> dict:
    try:
        table = str(arguments.get("table") or "").strip()
        if not table:
            return _text_content("Error: table is required", is_error=True)
        conn = arguments.get("conn") or None
        database = str(arguments.get("database") or "")
        limit = _positive_int_arg(arguments, "limit", 20, maximum=1000)

        _, _, _, profile = _ctx.get(conn)
        result = profile.sample_rows(table, database=database, limit=limit)
        rows, preview_meta = preview_rows(
            list(result.rows or []),
            columns=list(result.columns or []),
            max_rows=min(limit, 50),
        )
        data = {
            "columns": result.columns,
            "rows": rows,
            "row_preview": preview_meta,
            "row_count": result.row_count,
            "sql": result.sql,
        }
        return _data_content(data)
    except Exception as exc:
        return _text_content(f"Error: {exc}", is_error=True)


# ── Tool dispatch table ────────────────────────────────────────────────────

_TOOL_HANDLERS: dict[str, Any] = {
    "ask": handle_ask,
    "list_connections": handle_list_connections,
    "list_databases": handle_list_databases,
    "list_tables": handle_list_tables,
    "describe_table": handle_describe_table,
    "inspect_metadata": handle_inspect_metadata,
    "execute_sql": handle_execute_sql,
    "validate_sql": handle_validate_sql,
    "explain_sql": handle_explain_sql,
    "column_stats": handle_column_stats,
    "profile_table": handle_profile_table,
    "sample_rows": handle_sample_rows,
}


# ── Protocol handlers ──────────────────────────────────────────────────────

_active_mode: str = "full"


def handle_initialize(params: dict) -> dict:
    requested = str((params or {}).get("protocolVersion") or "")
    negotiated = requested if requested in _SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
    return {
        "protocolVersion": negotiated,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
        "instructions": (
            "DBAide is a read-only database assistant. All tools are safe to call — "
            "no data is modified. Typical workflow: "
            "list_connections → list_databases → list_tables → describe_table → execute_sql. "
            "Use column_stats for targeted metrics on specific columns, "
            "or profile_table for a comprehensive overview of all columns."
        ),
    }


def handle_tools_list(_params: dict) -> dict:
    tools: list[dict] = []
    if _active_mode in ("full", "ask"):
        tools.append(ASK_TOOL)
    if _active_mode in ("full", "tools"):
        tools.extend(_ATOMIC_TOOLS)
    return {"tools": tools}


def handle_tools_call(params: dict) -> dict:
    name = params.get("name", "")
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _text_content(f"Unknown tool: {name}", is_error=True)

    if _active_mode == "ask" and name != "ask":
        return _text_content(
            f"Tool '{name}' is not available in 'ask' mode", is_error=True,
        )
    if _active_mode == "tools" and name == "ask":
        return _text_content(
            f"Tool 'ask' is not available in 'tools' mode", is_error=True,
        )

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _text_content("Tool arguments must be an object", is_error=True)
    return handler(arguments)


def handle_ping(_params: dict) -> dict:
    return {}


HANDLERS = {
    "initialize": handle_initialize,
    "notifications/initialized": None,
    "ping": handle_ping,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


# ── Main loop ───────────────────────────────────────────────────────────────

# stdout is written from both the main stdin loop and background ask threads
# (progress notifications + the eventual result); serialize writes so two
# messages never interleave on the wire.
_send_lock = threading.Lock()

# In-flight async ``ask`` calls: request id → cancel Event, so a
# notifications/cancelled for that id can signal the running agent.
_inflight: dict[Any, "threading.Event"] = {}
_inflight_lock = threading.Lock()


def _send(msg: dict | list) -> None:
    try:
        with _send_lock:
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # In a worker thread SystemExit just ends the thread; the main loop's
        # next write will hit the same error and shut the server down.
        raise SystemExit(0)


def _handle_one(msg: Any) -> dict | None:
    """Dispatch a single JSON-RPC request object. Returns a response dict to
    send, or None for notifications (and malformed messages that carry no id)."""
    if not isinstance(msg, dict):
        # Malformed request (scalar, array element that isn't an object, …).
        # We have no id to correlate a response, so per JSON-RPC we drop it.
        return None

    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    handler = HANDLERS.get(method)
    if handler is None:
        if msg_id is not None and method not in HANDLERS:
            return _error(msg_id, -32601, f"Method not found: {method}")
        return None

    try:
        result = handler(params)
        if msg_id is not None:
            return _ok(msg_id, result)
        return None
    except Exception as exc:
        logger.exception("handler error for %s", method)
        if msg_id is not None:
            return _error(msg_id, -32603, str(exc))
        return None


def _spawn_ask(msg: dict) -> None:
    """Run the long-lived ``ask`` tool on a background thread so the main stdin
    loop stays responsive — it can still process notifications/cancelled (to abort
    the run) and other tool calls. Progress and the final result are written by
    the worker via the shared, locked ``_send``."""
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    progress_token = (params.get("_meta") or {}).get("progressToken") if isinstance(params.get("_meta"), dict) else None

    cancel_event = threading.Event()
    if msg_id is not None:
        with _inflight_lock:
            _inflight[msg_id] = cancel_event

    def run() -> None:
        try:
            result = handle_ask(arguments, progress_token=progress_token, cancel_event=cancel_event)
            if msg_id is not None and not cancel_event.is_set():
                # If the client cancelled, it has stopped waiting for a response
                # (per the MCP cancellation spec) — don't send a late one.
                _send(_ok(msg_id, result))
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("ask thread failed")
            if msg_id is not None and not cancel_event.is_set():
                _send(_error(msg_id, -32603, str(exc)))
        finally:
            if msg_id is not None:
                with _inflight_lock:
                    _inflight.pop(msg_id, None)

    threading.Thread(target=run, name="dbaide-mcp-ask", daemon=True).start()


def _is_async_ask(msg: Any) -> bool:
    """True for a tools/call request targeting ``ask`` when ask is enabled in the
    active mode (so it should run on a background thread)."""
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return False
    params = msg.get("params") or {}
    if not isinstance(params, dict) or params.get("name") != "ask":
        return False
    return _active_mode in ("full", "ask")


def serve(*, mode: str = "full") -> None:
    """Run the MCP server on stdio (blocking).

    Args:
        mode: "full" (ask + tools), "ask" (Mode A only), "tools" (Mode B only).
    """
    global _active_mode
    _active_mode = mode if mode in ("full", "ask", "tools") else "full"

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    signal.signal(signal.SIGTERM, lambda *_: signal.raise_signal(signal.SIGINT))

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # JSON-RPC 2.0 batch: an array of request objects. Respond with an
            # array of the non-notification results (or nothing if all were
            # notifications). A non-dict scalar is dropped without crashing.
            if isinstance(msg, list):
                responses = [r for r in (_handle_one(m) for m in msg) if r is not None]
                if responses:
                    _send(responses)
                continue

            # Cancellation: signal the matching in-flight ask so its agent loop
            # aborts at the next cancel_check.
            if isinstance(msg, dict) and msg.get("method") == "notifications/cancelled":
                req_id = (msg.get("params") or {}).get("requestId")
                with _inflight_lock:
                    ev = _inflight.get(req_id)
                if ev is not None:
                    ev.set()
                continue

            # Long-running ask runs on a worker thread so the loop stays
            # responsive to cancellation and other calls.
            if _is_async_ask(msg):
                _spawn_ask(msg)
                continue

            response = _handle_one(msg)
            if response is not None:
                _send(response)

    except (KeyboardInterrupt, SystemExit):
        pass
    except (BrokenPipeError, OSError):
        pass


if __name__ == "__main__":
    import argparse as _ap
    _p = _ap.ArgumentParser()
    _p.add_argument("--mode", choices=["full", "ask", "tools"], default="full")
    _a = _p.parse_args()
    serve(mode=_a.mode)
