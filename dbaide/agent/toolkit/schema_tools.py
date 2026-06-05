"""Schema discovery / disclosure tools."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    DISCOVER_SCHEMA, RESOLVE_SCHEMA, SYNTHESIZE_SCHEMA_ANSWER,
    LIST_DATABASES, LIST_TABLES, DESCRIBE_TABLE,
)
from dbaide.agent.schema_context import normalize_db_table, object_notes_for_tables
from dbaide.agent.toolkit.support import (
    _err, _note_working_db, _remember_table_schema, _disclosed_table_names,
)

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
                {"kind": h.kind, "path": h.path, "name": h.name, "database": h.database, "summary": h.summary[:240]}
                for h in discovery.hits
            ]
            return ToolResult(ok=True, data={"hits": hits, "trace": discovery.trace, "count": len(hits)})
        except Exception as exc:
            logger.warning("discover_schema_failed: %s", exc)
            return ToolResult(ok=False, error=_err("discover_schema", str(exc), retryable=True))

    def _resolve_schema(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.schema_link import SchemaLinker

        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        database = str(args.get("database") or orchestrator.run_state.database or "")
        if not question:
            return ToolResult(ok=False, error=_err("resolve_schema", "question is required"))
        try:
            resolved = SchemaLinker(orchestrator).resolve(question, database=database)
        except Exception as exc:
            logger.warning("resolve_schema_failed: %s", exc)
            return ToolResult(ok=False, error=_err("resolve_schema", str(exc), retryable=True))
        # Ambiguous → surface as a user question (same pause/resume path as ask_user).
        if resolved.pending_question:
            orchestrator.run_state.pending_question = resolved.pending_question
            orchestrator.run_state.pending_options = list(resolved.pending_options)
            orchestrator.run_state.pending_questions = [
                {"ask": resolved.pending_question, "options": list(resolved.pending_options)}
            ]
            return ToolResult(ok=True, data={
                "pending": True, "question": resolved.pending_question,
                "options": resolved.pending_options,
            })
        orchestrator.run_state.resolved_schema = resolved
        orchestrator.run_state.relations = list(resolved.joins)
        # Remember the resolved tables so generate_sql / validation see them.
        for db, table, columns in resolved.to_disclosed():
            _remember_table_schema(orchestrator, table, db, columns)
        tables_payload = [
            {
                "database": t["database"], "table": t["table"],
                "columns": [c.name for c in t["columns"]],
                "reason": t.get("reason", ""),
            }
            for t in resolved.tables
        ]
        return ToolResult(ok=True, data={
            "tables": tables_payload,
            "joins": resolved.joins,
            "sufficient": resolved.sufficient,
            "summary": resolved.summary_line(),
            "notes": resolved.notes,
        })

    def _synthesize_schema_answer(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.progressive_schema import ProgressiveSchemaAgent

        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", "question is required"))
        try:
            discovery = orchestrator.run_state.discovery or orchestrator._discover(question, parent=orchestrator.run_state.trace_node)
            agent = ProgressiveSchemaAgent(orchestrator.llm, orchestrator.asset_store, orchestrator.instance)
            pairs = list({
                (str(getattr(h, "database", "") or ""), str(getattr(h, "table", "") or ""))
                for h in discovery.hits if getattr(h, "table", "")
            })
            answer = agent.synthesize_answer(
                question,
                discovery,
                progress=orchestrator.progress,
                parent=orchestrator.run_state.trace_node,
                object_notes=object_notes_for_tables(orchestrator, pairs),
            )
            orchestrator.run_state.answer = answer
            return ToolResult(ok=True, data={"answer": answer})
        except Exception as exc:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", str(exc), retryable=True))

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
        database, table = normalize_db_table(table, database)
        columns = orchestrator.schema.describe_table(table, database=database)
        _remember_table_schema(orchestrator, table, database, columns)
        payload = [
            {
                "name": c.name,
                "data_type": c.data_type,
                "nullable": c.nullable,
                "primary_key": c.primary_key,
                "indexed": c.indexed,
                "comment": (c.comment or "")[:120],
            }
            for c in columns
        ]
        data: dict[str, Any] = {
            "table": table,
            "database": database,
            "columns": payload,
            "disclosed_tables": _disclosed_table_names(orchestrator),
        }
        # The table is the disclosure leaf: surface its indexes, FKs, row-count and
        # a small sample from the offline doc in this one call, when assets exist.
        tdoc = orchestrator.asset_store.table_doc(orchestrator.instance, database, table)
        if tdoc:
            if tdoc.get("indexes"):
                data["indexes"] = tdoc["indexes"]
            if tdoc.get("foreign_keys"):
                data["foreign_keys"] = tdoc["foreign_keys"]
            if tdoc.get("row_count") is not None:
                data["row_count"] = tdoc["row_count"]
            if tdoc.get("sample_rows"):
                data["sample_rows"] = tdoc["sample_rows"]
        return ToolResult(ok=True, data=data)

    registry.register(DISCOVER_SCHEMA, _discover_schema)
    registry.register(RESOLVE_SCHEMA, _resolve_schema)
    registry.register(SYNTHESIZE_SCHEMA_ANSWER, _synthesize_schema_answer)
    registry.register(LIST_DATABASES, _list_databases)
    registry.register(LIST_TABLES, _list_tables)
    registry.register(DESCRIBE_TABLE, _describe_table)
