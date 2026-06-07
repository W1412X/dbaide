"""Schema discovery / disclosure tools."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    DISCOVER_SCHEMA, RETRIEVE_SCHEMA_CONTEXT,
    LIST_DATABASES, LIST_TABLES, DESCRIBE_TABLE,
)
from dbaide.agent.schema_context import apply_column_notes, object_notes_for_tables
from dbaide.agent.toolkit.support import (
    _err, _note_working_db, _remember_table_schema, _disclosed_table_names,
    _normalize_tool_table, _string_list,
)
from dbaide.models import ColumnInfo

logger = logging.getLogger("dbaide.agent.toolkit")


def register(registry: ToolRegistry, orchestrator) -> None:
    def _discover_schema(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("discover_schema", "question is required"))
        try:
            discovery = orchestrator._discover(question, parent=orchestrator.run_state.trace_node)
            orchestrator.run_state.discovery = discovery
            # If discovery points at exactly one database, narrow the working scope to
            # it so SQL generation/execution target it (not the connection default).
            hit_dbs = {h.database for h in discovery.hits if h.database}
            if len(hit_dbs) == 1:
                _note_working_db(orchestrator, next(iter(hit_dbs)))
            hits = [
                {
                    "kind": h.kind,
                    "path": h.path,
                    "name": h.name,
                    "database": h.database,
                    "table": h.table,
                    "summary": h.summary[:240],
                    "reason": h.reason,
                    "note": h.note,
                }
                for h in discovery.hits
            ]
            return ToolResult(ok=True, data={"hits": hits, "trace": discovery.trace, "count": len(hits)})
        except Exception as exc:
            logger.warning("discover_schema_failed: %s", exc)
            return ToolResult(ok=False, error=_err("discover_schema", str(exc), retryable=True))

    def _retrieve_schema_context(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.schema_link import SchemaEvidenceRetriever

        request = str(args.get("request") or args.get("question") or orchestrator.run_state.question or "").strip()
        database = str(args.get("database") or orchestrator.run_state.database or "")
        if not request:
            return ToolResult(ok=False, error=_err("retrieve_schema_context", "request is required"))
        try:
            report = SchemaEvidenceRetriever(orchestrator).retrieve(
                request,
                database=database,
                focus_terms=_string_list(args.get("focus_terms")),
                scope=args.get("scope") if isinstance(args.get("scope"), dict) else None,
                need=str(args.get("need") or ""),
                limit=int(args.get("limit") or 8),
            )
        except Exception as exc:
            logger.warning("retrieve_schema_context_failed: %s", exc)
            return ToolResult(ok=False, error=_err("retrieve_schema_context", str(exc), retryable=True))
        return ToolResult(ok=True, data=report.to_tool_data())

    def _list_databases(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        dbs = orchestrator.schema.list_databases()
        return ToolResult(ok=True, data={"databases": dbs})

    def _list_tables(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        # Listing a specific database narrows the agent's working scope to it.
        _note_working_db(orchestrator, database)
        tables = orchestrator.schema.list_tables(database=database)
        payload = [{"name": t.name, "comment": (t.comment or "")[:120]} for t in tables[:50]]
        return ToolResult(ok=True, data={"database": database, "tables": payload})

    def _describe_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not table:
            return ToolResult(ok=False, error=_err("describe_table", "table is required"))
        # Tolerate a db-qualified name like "platform.sys_user" (with empty database) —
        # split it so the catalog lookup finds the real table instead of returning
        # empty columns and sending the model into a re-describe loop.
        database, table = _normalize_tool_table(orchestrator, table, database)
        tdoc = orchestrator.asset_store.table_doc(
            orchestrator.instance,
            database,
            table,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        columns = orchestrator.schema.describe_table(table, database=database)
        if not columns and tdoc and tdoc.get("columns"):
            columns = [
                ColumnInfo(
                    name=str(col.get("name") or ""),
                    data_type=str(col.get("data_type") or ""),
                    nullable=col.get("nullable"),
                    default=col.get("default"),
                    comment=str(col.get("comment") or ""),
                    primary_key=bool(col.get("primary_key")),
                    indexed=bool(col.get("indexed")),
                )
                for col in tdoc.get("columns") or []
                if col.get("name")
            ]
        if not columns:
            target = f"{database}.{table}" if database else table
            return ToolResult(
                ok=False,
                error=_err(
                    "describe_table",
                    f"table not found or has no readable columns: {target}",
                ),
                data={"table": table, "database": database, "columns": []},
            )
        apply_column_notes(orchestrator, [(database, table, columns)])
        _remember_table_schema(orchestrator, table, database, columns)
        payload = [
            {
                "name": c.name,
                "data_type": c.data_type,
                "nullable": c.nullable,
                "primary_key": c.primary_key,
                "indexed": c.indexed,
                "comment": (c.comment or "")[:120],
                "note": c.note,
            }
            for c in columns
        ]
        data: dict[str, Any] = {
            "table": table,
            "database": database,
            "columns": payload,
            "disclosed_tables": _disclosed_table_names(orchestrator),
        }
        object_notes = object_notes_for_tables(orchestrator, [(database, table)])
        if object_notes:
            data["object_notes"] = object_notes
        # Full table description includes intrinsic table metadata. This is not an
        # automatic relation workflow: it is the expected payload for a direct
        # describe_table request.
        if tdoc:
            if tdoc.get("indexes"):
                data["indexes"] = tdoc["indexes"]
            if tdoc.get("foreign_keys"):
                data["foreign_keys"] = tdoc["foreign_keys"]
            if tdoc.get("row_count") is not None:
                data["row_count"] = tdoc["row_count"]
            if tdoc.get("sample_rows"):
                data["sample_rows"] = tdoc["sample_rows"]
        else:
            try:
                indexes = [idx.to_dict() if hasattr(idx, "to_dict") else idx for idx in orchestrator.adapter.indexes(table, database=database)]
                if indexes:
                    data["indexes"] = indexes
            except Exception:
                pass
            try:
                fks = [
                    {
                        "table": fk.table,
                        "column": fk.column,
                        "ref_table": fk.ref_table,
                        "ref_column": fk.ref_column,
                        "source": "foreign_key",
                    }
                    for fk in orchestrator.schema.foreign_keys(table, database=database)
                ]
                if fks:
                    data["foreign_keys"] = fks
            except Exception:
                pass
        return ToolResult(ok=True, data=data)

    registry.register(DISCOVER_SCHEMA, _discover_schema)
    registry.register(RETRIEVE_SCHEMA_CONTEXT, _retrieve_schema_context)
    registry.register(LIST_DATABASES, _list_databases)
    registry.register(LIST_TABLES, _list_tables)
    registry.register(DESCRIBE_TABLE, _describe_table)
