"""Tool handlers wired to AskOrchestrator services."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.core.result import ExecutionPolicy
from dbaide.models import ColumnInfo
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.agent.schema_context import (
    apply_column_notes,
    collect_relations,
    disclosed_schemas_for_tables,
    join_confidence_for_sql,
    merge_sql_context,
    normalize_db_table,
    object_notes_for_tables,
    validation_feedback,
)
from dbaide.agent.join_validation import validate_join_relations
from dbaide.joins import JoinCatalogStore, USER_JOIN_CONFIDENCE, catalog_record_to_relation
from dbaide.agent.progress_events import progress_event, subagent_event
from dbaide.tools.specs import (
    ASK_USER,
    CLARIFY_SEMANTICS,
    DESCRIBE_TABLE,
    DISCOVER_SCHEMA,
    EXECUTE_READONLY_SQL,
    EXECUTE_SQL,
    EXPLAIN_SQL,
    GENERATE_SQL,
    GET_RELATIONS,
    LIST_DATABASES,
    LIST_TABLES,
    COLUMN_STATS,
    PROFILE_TABLE,
    RESOLVE_SCHEMA,
    SYNTHESIZE_SCHEMA_ANSWER,
    VALIDATE_JOINS,
    VALIDATE_SQL,
    LIST_JOINS,
    ADD_JOIN,
    UPDATE_JOIN,
    DELETE_JOIN,
    ANNOTATE_OBJECT,
)

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.toolkit")

# Tools exposed to the Ask loop LLM (catalog CRUD stays on GUI/service only).
LOOP_DECISION_TOOL_NAMES = frozenset({
    "discover_schema",
    "resolve_schema",
    "synthesize_schema_answer",
    "list_databases",
    "list_tables",
    "describe_table",
    "get_relations",
    "clarify_semantics",
    "generate_sql",
    "validate_sql",
    "execute_sql",
    "execute_readonly_sql",
    "explain_sql",
    "profile_table",
    "column_stats",
    "ask_user",
    "annotate_object",
})


def loop_tool_specs(registry: ToolRegistry) -> list:
    return [s for s in registry.list_specs() if s.name in LOOP_DECISION_TOOL_NAMES]


def build_tool_registry(orchestrator: AskOrchestrator) -> ToolRegistry:
    """Register all agent tools bound to an orchestrator instance."""
    registry = ToolRegistry()

    def _discover_schema(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("discover_schema", "question is required"))
        try:
            discovery = orchestrator._discover(question, parent=orchestrator._loop_trace_node)
            orchestrator._loop_discovery = discovery
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

        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        database = str(args.get("database") or orchestrator._loop_database or "")
        if not question:
            return ToolResult(ok=False, error=_err("resolve_schema", "question is required"))
        try:
            resolved = SchemaLinker(orchestrator).resolve(question, database=database)
        except Exception as exc:
            logger.warning("resolve_schema_failed: %s", exc)
            return ToolResult(ok=False, error=_err("resolve_schema", str(exc), retryable=True))
        # Ambiguous → surface as a user question (same pause/resume path as ask_user).
        if resolved.pending_question:
            orchestrator._loop_pending_question = resolved.pending_question
            orchestrator._loop_pending_options = list(resolved.pending_options)
            orchestrator._loop_pending_questions = [
                {"ask": resolved.pending_question, "options": list(resolved.pending_options)}
            ]
            return ToolResult(ok=True, data={
                "pending": True, "question": resolved.pending_question,
                "options": resolved.pending_options,
            })
        orchestrator._loop_resolved_schema = resolved
        orchestrator._loop_relations = list(resolved.joins)
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

        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", "question is required"))
        try:
            discovery = orchestrator._loop_discovery or orchestrator._discover(question, parent=orchestrator._loop_trace_node)
            agent = ProgressiveSchemaAgent(orchestrator.llm, orchestrator.asset_store, orchestrator.instance)
            pairs = list({
                (str(getattr(h, "database", "") or ""), str(getattr(h, "table", "") or ""))
                for h in discovery.hits if getattr(h, "table", "")
            })
            answer = agent.synthesize_answer(
                question,
                discovery,
                progress=orchestrator.progress,
                parent=orchestrator._loop_trace_node,
                object_notes=object_notes_for_tables(orchestrator, pairs),
            )
            orchestrator._loop_answer = answer
            return ToolResult(ok=True, data={"answer": answer})
        except Exception as exc:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", str(exc), retryable=True))

    def _list_databases(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        dbs = orchestrator.schema.list_databases()
        return ToolResult(ok=True, data={"databases": dbs})

    def _list_tables(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
        # Listing a specific database narrows the agent's working scope to it.
        _note_working_db(orchestrator, database)
        tables = orchestrator.schema.list_tables(database=database)
        payload = [{"name": t.name, "comment": (t.comment or "")[:120]} for t in tables[:50]]
        return ToolResult(ok=True, data={"database": database, "tables": payload})

    def _describe_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or "").strip()
        database = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
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

    def _get_relations(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database_default = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
        tables_arg = args.get("tables")
        targets: list[tuple[str, str]] = []
        if isinstance(tables_arg, list) and tables_arg:
            for raw in tables_arg:
                name = str(raw).strip()
                if name:
                    targets.append((database_default, name))
        elif orchestrator._loop_schemas:
            for key in orchestrator._loop_schemas:
                db = orchestrator._loop_schema_db.get(key, database_default)
                table = key.split(".", 1)[1] if "." in key else key
                targets.append((db, table))
        else:
            table = str(args.get("table") or orchestrator._loop_table or "").strip()
            if table:
                targets.append((database_default, table))
        if not targets:
            return ToolResult(ok=False, error=_err("get_relations", "tables required (describe_table first)"))
        schemas = disclosed_schemas_for_tables(orchestrator, targets)
        sample_size = int(args.get("sample_size") or 150)
        # Eager auto-loading passes infer_semantic=False: declared FKs + catalog are
        # cheap, but LLM semantic inference is expensive (~tens of seconds) and must
        # not run just because two tables happen to be disclosed — only on demand for
        # the tables a query actually joins (generate_sql triggers that itself).
        infer_semantic = bool(args.get("infer_semantic", True))
        relations = collect_relations(
            orchestrator,
            targets,
            question=orchestrator._loop_question,
            disclosed_schemas=schemas,
            sample_size=sample_size,
            infer_semantic=infer_semantic,
            parent=orchestrator._loop_trace_node,
        )
        orchestrator._loop_relations = relations
        _persist_agent_joins(orchestrator, relations, database=targets[0][0] if targets else "")
        return ToolResult(ok=True, data=_relations_payload(relations))

    def _validate_joins(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        relations = list(orchestrator._loop_relations or [])
        if not relations:
            return ToolResult(ok=False, error=_err("validate_joins", "no relations; call get_relations first"))
        targets = _targets_from_relations(orchestrator, relations)
        if len({t for _, t in targets}) < 2:
            return ToolResult(ok=False, error=_err("validate_joins", "need at least two tables"))
        schemas = disclosed_schemas_for_tables(orchestrator, targets)
        sample_size = int(args.get("sample_size") or 150)
        validated = validate_join_relations(
            orchestrator,
            relations,
            schemas,
            sample_size=sample_size,
            parent="validate_joins",
        )
        orchestrator._loop_relations = validated
        _persist_agent_joins(orchestrator, validated, database=targets[0][0] if targets else "")
        return ToolResult(ok=True, data=_relations_payload(validated))

    def _list_joins(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database = str(args.get("database") or orchestrator._loop_database or "")
        tables_arg = args.get("tables")
        tables: list[str] | None = None
        if isinstance(tables_arg, list):
            tables = [str(t).strip() for t in tables_arg if str(t).strip()]
        min_conf = float(args.get("min_confidence") or 0.0)
        endpoint = args if args.get("table") and args.get("column") else None
        records = orchestrator.join_catalog.list_records(
            orchestrator.instance,
            database=database,
            tables=tables,
            min_confidence=min_conf,
            endpoint=endpoint,
        )
        return ToolResult(ok=True, data={"joins": records, "count": len(records)})

    def _add_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        required = ("table", "column", "ref_table", "ref_column")
        missing = [k for k in required if not str(args.get(k) or "").strip()]
        if missing:
            return ToolResult(ok=False, error=_err("add_join", f"missing: {', '.join(missing)}"))
        database = str(args.get("database") or orchestrator._loop_database or "")
        source = str(args.get("source") or "user").strip().lower()
        if source not in {"user", "agent"}:
            source = "user"
        rel = {
            "table": str(args["table"]).strip(),
            "column": str(args["column"]).strip(),
            "ref_table": str(args["ref_table"]).strip(),
            "ref_column": str(args["ref_column"]).strip(),
            "join_type": str(args.get("join_type") or ""),
            "reason": str(args.get("reason") or ""),
            "confidence": USER_JOIN_CONFIDENCE if source == "user" else float(args.get("confidence") or 0.7),
        }
        record = orchestrator.join_catalog.add(
            orchestrator.instance,
            rel,
            source=source,
            database=database,
        )
        return ToolResult(ok=True, data={"join": record, "relation": catalog_record_to_relation(record)})

    def _annotate_object(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        note = str(args.get("note") or "").strip()
        if not note:
            return ToolResult(ok=False, error=_err("annotate_object", "note is required"))
        table = str(args.get("table") or "").strip()
        column = str(args.get("column") or "").strip()
        scope = str(args.get("scope") or "").strip().lower()
        if scope not in {"database", "table", "column"}:
            scope = "column" if column else ("table" if table else "database")
        database = str(args.get("database") or orchestrator._loop_database or "")
        store = getattr(orchestrator, "annotations", None)
        if store is None:
            return ToolResult(ok=False, error=_err("annotate_object", "annotation store unavailable"))
        try:
            record = store.add(
                orchestrator.instance,
                scope=scope,
                note=note,
                database=database,
                table=table,
                column=column,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=_err("annotate_object", str(exc)))
        return ToolResult(ok=True, data={"annotation": record, "saved": True})

    def _update_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        join_id = str(args.get("id") or args.get("join_id") or "").strip()
        if not join_id:
            return ToolResult(ok=False, error=_err("update_join", "id is required"))
        updated = orchestrator.join_catalog.update(orchestrator.instance, join_id, args)
        if updated is None:
            return ToolResult(ok=False, error=_err("update_join", f"join not found: {join_id}"))
        return ToolResult(ok=True, data={"join": updated, "relation": catalog_record_to_relation(updated)})

    def _delete_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        join_id = str(args.get("id") or args.get("join_id") or "").strip()
        endpoint = None
        if args.get("table") and args.get("column") and args.get("ref_table") and args.get("ref_column"):
            endpoint = {
                "table": args["table"],
                "column": args["column"],
                "ref_table": args["ref_table"],
                "ref_column": args["ref_column"],
            }
        if not join_id and not endpoint:
            return ToolResult(ok=False, error=_err("delete_join", "id or full endpoint required"))
        ok = orchestrator.join_catalog.delete(orchestrator.instance, join_id=join_id, endpoint=endpoint)
        if not ok:
            return ToolResult(ok=False, error=_err("delete_join", "join not found"))
        return ToolResult(ok=True, data={"deleted": True, "id": join_id or "endpoint"})

    def _clarify_semantics(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.clarify import SemanticClarifier

        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        resolved = getattr(orchestrator, "_loop_resolved_schema", None)
        if resolved is not None and not resolved.is_empty():
            disclosed = resolved.to_disclosed()
        else:
            disclosed = _collect_disclosed_schemas(orchestrator, {})
        if not disclosed:
            # Nothing resolved yet — resolve the schema first; don't block.
            return ToolResult(ok=True, data={"clear": True, "note": "no schema resolved yet"})
        # Clarification must see the COMPLETE column list of the relevant tables. The
        # resolved schema keeps only the minimal-necessary columns, which would force
        # the model to ask about a "which column?" candidate it can't actually see —
        # the cause of fabricated field names. Expand each table to its full columns.
        disclosed = _expand_to_full_columns(orchestrator, disclosed)
        apply_column_notes(orchestrator, disclosed)  # user column notes visible to clarifier
        observed = _sample_observed_values(orchestrator, disclosed)
        object_notes = object_notes_for_tables(
            orchestrator, [(db, tbl) for db, tbl, _ in disclosed]
        )
        try:
            plan = SemanticClarifier(orchestrator.llm).analyze(
                question, disclosed, observed,
                already_confirmed=list(getattr(orchestrator, "_loop_clarifications", [])),
                object_notes=object_notes,
            )
        except Exception as exc:  # noqa: BLE001 — clarification must never break a query
            logger.debug("clarify_semantics failed: %s", exc, exc_info=True)
            return ToolResult(ok=True, data={"clear": True})
        # Assumptions are applied whether or not we ask, so SQL generation honours them.
        if plan.assumptions:
            orchestrator._loop_clarifications.extend(plan.assumptions)
        if plan.is_empty():
            return ToolResult(ok=True, data={"clear": True, "assumptions": plan.assumptions})
        # Material ambiguity → pause and confirm the exact criteria with the user.
        rendered = plan.render_question()
        orchestrator._loop_clarify_questions = rendered
        orchestrator._loop_pending_question = rendered
        orchestrator._loop_pending_options = plan.first_options()
        # Structured per-question list so the UI can step through them one at a time.
        orchestrator._loop_pending_questions = [
            {"ask": str(q.get("ask") or ""), "options": list(q.get("options") or [])}
            for q in plan.questions
        ]
        return ToolResult(ok=True, data={
            "pending": True, "question": rendered, "options": plan.first_options(),
        })

    def _generate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        # Prefer the minimal-necessary schema from resolve_schema: generating SQL on
        # only the relevant tables/columns is more accurate than the full disclosure
        # (the irrelevant schema is noise). Fall back to the full disclosure if the
        # model named specific tables in args, or no resolved schema exists.
        resolved = getattr(orchestrator, "_loop_resolved_schema", None)
        if resolved is not None and not resolved.is_empty() and not args.get("table") and not args.get("tables"):
            disclosed = resolved.to_disclosed()
        else:
            disclosed = _collect_disclosed_schemas(orchestrator, args)
        if not disclosed:
            return ToolResult(ok=False, error=_err("generate_sql", "table is required (resolve_schema or describe_table first)"))
        # Backfill user column notes regardless of how `disclosed` was built (the
        # resolve_schema fast path bypasses _disclosed_schemas_for_tables).
        apply_column_notes(orchestrator, disclosed)
        try:
            targets = [(db, table) for db, table, _ in disclosed]
            relations = list(orchestrator._loop_relations or [])
            if not relations and len(targets) >= 2:
                relations = collect_relations(
                    orchestrator,
                    targets,
                    question=question,
                    disclosed_schemas=disclosed,
                    parent=orchestrator._loop_trace_node,
                )
                orchestrator._loop_relations = relations
            ctx = merge_sql_context(orchestrator.session.disclosure.summary(), relations)
            if getattr(orchestrator, "_loop_clarifications", None):
                ctx["criteria"] = list(orchestrator._loop_clarifications)  # confirmed 口径
            object_notes = object_notes_for_tables(orchestrator, targets)
            if object_notes:
                ctx["object_notes"] = object_notes  # authoritative db/table user notes
            feedback = orchestrator._loop_sql_feedback
            table_names = ", ".join(t for _, t, _ in disclosed)
            orchestrator.progress(
                subagent_event(
                    agent="sql_writer",
                    title="Generating SQL",
                    parent="generate_sql",
                    detail=table_names,
                ),
            )
            if len(disclosed) == 1:
                database, table, columns = disclosed[0]
                draft = orchestrator.sql_writer.write(
                    question,
                    table,
                    columns,
                    context=ctx,
                    feedback=feedback,
                )
            else:
                draft = orchestrator.sql_writer.write(
                    question,
                    disclosed_schemas=disclosed,
                    context=ctx,
                    feedback=feedback,
                )
            orchestrator._loop_sql_feedback = ""
            orchestrator._loop_sql = draft.sql
            orchestrator._loop_sql_rationale = draft.rationale
            orchestrator._loop_sql_confidence = draft.confidence
            orchestrator.progress(
                subagent_event(
                    agent="sql_writer",
                    title="SQL draft ready",
                    parent="generate_sql",
                    detail=draft.rationale[:160] if draft.rationale else "",
                    status="completed",
                ),
            )
            tables_used = [t for _, t, _ in disclosed]
            if tables_used:
                orchestrator._loop_table = tables_used[0]
                _note_working_db(orchestrator, disclosed[0][0])
            return ToolResult(
                ok=True,
                data={
                    "sql": draft.sql,
                    "rationale": draft.rationale,
                    "confidence": draft.confidence,
                    "tables": tables_used,
                },
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("generate_sql", str(exc), retryable=True))

    def _validate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator._loop_sql or "").strip()
        if not sql:
            return ToolResult(ok=False, error=_err("validate_sql", "sql is required"))
        report = orchestrator.query.validate_sql_report(sql, add_limit=True)
        if report.ok:
            orchestrator._loop_sql = report.normalized_sql
        else:
            orchestrator._loop_sql_feedback = validation_feedback(report.issues)
        return ToolResult(
            ok=report.ok,
            data={
                "ok": report.ok,
                "normalized_sql": report.normalized_sql,
                "issues": [{"message": i, "severity": "error"} for i in report.issues],
                "risk_level": report.risk_level,
                "warnings": report.warnings,
                "requires_confirmation": report.requires_confirmation,
            },
        )

    def _execute_sql(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator._loop_sql or "").strip()
        database = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("execute_sql", "sql is required"))

        policy = ExecutionPolicy(ctx.execution_policy) if ctx.execution_policy else orchestrator.execution_policy
        if policy in {ExecutionPolicy.INSPECT_ONLY, ExecutionPolicy.SQL_ONLY}:
            return ToolResult(
                ok=False,
                data={"blocked": True, "reason": f"Execution blocked by policy: {policy.value}"},
            )
        if not orchestrator._loop_execute_allowed:
            return ToolResult(ok=False, data={"blocked": True, "reason": "Execution disabled for this request"})

        validation = orchestrator.query.validate_sql(sql, add_limit=True)
        if not validation.ok:
            issues = "; ".join(i.message for i in validation.issues)
            return ToolResult(ok=False, error=_err("execute_sql", f"SQL invalid: {issues}"))

        validation_report = orchestrator.query.validate_sql_report(validation.normalized_sql, add_limit=False)
        confidence = float(orchestrator._loop_sql_confidence or 0.7)
        has_joins = " join " in validation.normalized_sql.lower()
        join_conf = (
            join_confidence_for_sql(orchestrator._loop_relations, validation.normalized_sql)
            if has_joins
            else 1.0
        )
        # Pre-execution cost gate: estimate the scan size via EXPLAIN.
        policy_obj = getattr(orchestrator.adapter, "policy", None)
        explain_max_rows = getattr(orchestrator.query, "explain_max_rows", 0)
        max_join_tables = policy_obj.max_join_tables if policy_obj else 2
        estimated_rows = None
        if explain_max_rows:
            estimated_rows = orchestrator.query.estimate_rows(validation.normalized_sql, database=database)
            if estimated_rows is not None:
                orchestrator.progress(
                    subagent_event(
                        agent="explain",
                        title=f"EXPLAIN ~{estimated_rows:,} rows",
                        parent="execute_sql",
                        detail=f"cost gate limit {explain_max_rows:,}",
                        status="completed" if estimated_rows <= explain_max_rows else "info",
                    ),
                )
        risk = orchestrator.risk.decide(
            policy=policy,
            validation=validation_report,
            plan_confidence=confidence,
            table_count=max(1, len(_tables_in_sql(validation.normalized_sql))),
            has_joins=has_joins,
            join_confidence=join_conf,
            estimated_rows=estimated_rows,
            explain_max_rows=explain_max_rows,
            max_join_tables=max_join_tables,
        )
        orchestrator.progress(
            subagent_event(
                agent="risk",
                title=f"Risk: {risk.action}",
                parent="execute_sql",
                detail=risk.reason,
                status="completed" if risk.action == "auto_execute" else "info",
            ),
        )
        if risk.action != "auto_execute":
            return ToolResult(
                ok=False,
                data={
                    "blocked": True,
                    "reason": risk.reason,
                    "risk_action": risk.action,
                    "risk_level": risk.risk_level,
                    "sql": validation.normalized_sql,
                    "warnings": validation_report.warnings,
                },
            )

        try:
            result = orchestrator.query.execute_sql(
                validation.normalized_sql,
                database=database,
                limit=orchestrator.session.default_limit,
            )
            orchestrator._loop_query_result = result
            orchestrator._loop_sql = validation.normalized_sql
            orchestrator._loop_sql_feedback = ""
            orchestrator.progress(
                subagent_event(
                    agent="sql",
                    title=f"Executed · {result.row_count} rows · {result.elapsed_ms:.0f}ms",
                    parent="execute_sql",
                    detail=validation.normalized_sql,
                    status="completed",
                ),
            )
            return ToolResult(
                ok=True,
                data={
                    "columns": result.columns,
                    "rows": result.rows[:20],
                    "row_count": result.row_count,
                    "truncated": result.truncated,
                    "elapsed_ms": result.elapsed_ms,
                    "sql": validation.normalized_sql,
                },
            )
        except Exception as exc:
            orchestrator._loop_sql_feedback = str(exc)
            return ToolResult(ok=False, error=_err("execute_sql", str(exc), retryable=True))

    def _explain_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator._loop_sql or "").strip()
        database = str(args.get("database") or orchestrator._loop_database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("explain_sql", "sql is required"))
        report = orchestrator.diagnose.diagnose_sql(sql, database=database)
        # Surface the diagnosis as the loop answer so it is not lost if the model
        # finishes without restating it (mirrors profile_table's behaviour).
        hints = report.get("issues") or report.get("hints") or []
        if hints:
            lines = [f"EXPLAIN diagnosis for:\n```sql\n{sql}\n```", ""]
            lines += [f"- {hint}" for hint in hints]
            orchestrator._loop_answer = "\n".join(lines)
        return ToolResult(ok=bool(report.get("ok")), data=report)

    def _profile_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator._loop_table or "").strip()
        database = str(args.get("database") or orchestrator._loop_table_database or "")
        if not table:
            return ToolResult(ok=False, error=_err("profile_table", "table is required"))
        columns = args.get("columns")
        if not columns:
            cols = orchestrator.schema.describe_table(table, database=database)
            columns = [c.name for c in cols[:8]]
        profiles = orchestrator.profile.profile_table(table, list(columns), database=database)
        answer = orchestrator.formatter.profiles(profiles)
        orchestrator._loop_answer = answer
        return ToolResult(ok=True, data={"answer": answer, "column_count": len(profiles)})

    def _column_stats(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator._loop_table or "").strip()
        database = str(args.get("database") or orchestrator._loop_table_database or "")
        if not table:
            return ToolResult(ok=False, error=_err("column_stats", "table is required"))
        columns = args.get("columns") if isinstance(args.get("columns"), list) else None
        metrics = args.get("metrics") if isinstance(args.get("metrics"), list) else None
        try:
            stats = orchestrator.profile.column_stats(
                table, columns, metrics=metrics, database=database,
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("column_stats", str(exc), retryable=True))
        return ToolResult(ok=True, data={"table": table, "columns": stats})

    def _ask_user(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("ask_user", "question is required"))
        options_raw = args.get("options")
        options: list[str] = []
        if isinstance(options_raw, list):
            options = [str(item).strip() for item in options_raw if str(item).strip()]
        orchestrator._loop_pending_question = question
        orchestrator._loop_pending_options = options
        orchestrator._loop_pending_questions = [{"ask": question, "options": options}]
        return ToolResult(
            ok=True,
            data={"pending": True, "question": question, "options": options},
        )

    registry.register(DISCOVER_SCHEMA, _discover_schema)
    registry.register(RESOLVE_SCHEMA, _resolve_schema)
    registry.register(SYNTHESIZE_SCHEMA_ANSWER, _synthesize_schema_answer)
    registry.register(LIST_DATABASES, _list_databases)
    registry.register(LIST_TABLES, _list_tables)
    registry.register(DESCRIBE_TABLE, _describe_table)
    registry.register(GET_RELATIONS, _get_relations)
    registry.register(VALIDATE_JOINS, _validate_joins)
    registry.register(LIST_JOINS, _list_joins)
    registry.register(ADD_JOIN, _add_join)
    registry.register(UPDATE_JOIN, _update_join)
    registry.register(DELETE_JOIN, _delete_join)
    registry.register(ANNOTATE_OBJECT, _annotate_object)
    registry.register(CLARIFY_SEMANTICS, _clarify_semantics)
    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_READONLY_SQL, _execute_sql)
    registry.register(EXECUTE_SQL, _execute_sql)
    registry.register(EXPLAIN_SQL, _explain_sql)
    registry.register(PROFILE_TABLE, _profile_table)
    registry.register(COLUMN_STATS, _column_stats)
    registry.register(ASK_USER, _ask_user)
    return registry


def _persist_agent_joins(
    orchestrator: AskOrchestrator,
    relations: list[dict[str, Any]],
    *,
    database: str = "",
) -> None:
    catalog = getattr(orchestrator, "join_catalog", None)
    if catalog is None:
        return
    try:
        saved = catalog.persist_agent_candidates(
            orchestrator.instance,
            relations,
            database=database or orchestrator._loop_database or "",
        )
        if saved:
            orchestrator.progress(
                subagent_event(
                    agent="join_catalog",
                    title=f"Saved {len(saved)} join candidate(s)",
                    parent="get_relations",
                ),
            )
    except Exception as exc:
        logger.warning("persist_agent_joins_failed: %s", exc)


def _relations_payload(relations: list[dict[str, Any]]) -> dict[str, Any]:
    declared = sum(1 for r in relations if str(r.get("source") or "") in {"foreign_key", "agent", "user"})
    semantic = sum(1 for r in relations if r.get("source") == "semantic")
    catalog = sum(1 for r in relations if r.get("catalog") or str(r.get("source") or "") in {"user", "agent"})
    validated = sum(1 for r in relations if float(r.get("confidence") or 0) >= 0.35)
    return {
        "relations": relations,
        "count": len(relations),
        "declared_count": declared,
        "semantic_count": semantic,
        "catalog_count": catalog,
        "validated_count": validated,
    }


def _targets_from_relations(orchestrator: AskOrchestrator, relations: list[dict[str, Any]]) -> list[tuple[str, str]]:
    db_default = orchestrator._loop_table_database or orchestrator._loop_database or ""
    names: set[str] = set()
    for rel in relations:
        for key in ("table", "ref_table"):
            name = str(rel.get(key) or "").strip()
            if name:
                names.add(name)
    targets: list[tuple[str, str]] = []
    for name in sorted(names):
        db = db_default
        for schema_key, schema_db in orchestrator._loop_schema_db.items():
            table_part = schema_key.split(".", 1)[1] if "." in schema_key else schema_key
            if table_part == name or schema_key == name:
                db = schema_db
                break
        targets.append((db, name))
    return targets


def _schema_key(database: str, table: str) -> str:
    db = database.strip()
    return f"{db}.{table}" if db else table


def _note_working_db(orchestrator: AskOrchestrator, database: str) -> None:
    """Record the database the agent has narrowed into, so subsequent tools default
    to *where the tables were found* — not the connection's default database — when
    the model omits the ``database`` argument. Never overwrite a known working db
    with an empty one."""
    db = (database or "").strip()
    if db:
        orchestrator._loop_table_database = db


_CATEGORICAL_TYPES = ("char", "text", "string", "enum", "varchar", "nchar", "nvarchar", "tinytext")


def _sample_observed_values(
    orchestrator: AskOrchestrator,
    disclosed: list[tuple[str, str, list[ColumnInfo]]],
    *,
    max_columns: int = 6,
    sample_rows: int = 300,
    max_distinct: int = 30,
    max_candidates: int = 12,
) -> dict[str, list[str]]:
    """Best-effort: the real distinct values of low-cardinality text columns in the
    resolved schema, so the clarifier asks about ACTUAL value encodings (e.g. which
    `delivery_status` means "妥投") instead of guessing one. Bounded and never fatal:
    reads a small sample (not a full DISTINCT scan), caps columns/rows, swallows
    errors, and is skipped when execution isn't allowed.

    The per-column sample reads run CONCURRENTLY (bounded) — they're independent
    SELECT … LIMIT probes, so doing them sequentially just stacked latency before
    every clarification."""
    if not getattr(orchestrator, "_loop_execute_allowed", False):
        return {}
    candidates: list[tuple[str, str, str]] = []  # (db, table, column)
    for db, table, columns in disclosed:
        for col in columns:
            dtype = (getattr(col, "data_type", "") or "").lower()
            name = getattr(col, "name", "")
            if not name or not name.replace("_", "").isalnum():
                continue  # only plain identifiers (no quoting headaches across dialects)
            if not any(k in dtype for k in _CATEGORICAL_TYPES):
                continue
            candidates.append((db, table, name))
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
    if not candidates:
        return {}

    def _probe(item: tuple[str, str, str]) -> tuple[str, list[str] | None]:
        db, table, name = item
        qualified = f"{db}.{table}" if db else table
        try:
            result = orchestrator.query.execute_sql(
                f"SELECT {name} FROM {qualified} LIMIT {sample_rows}",
                database=db, limit=sample_rows,
            )
        except Exception:  # noqa: BLE001 — grounding is optional
            return f"{table}.{name}", None
        seen: list[str] = []
        for row in getattr(result, "rows", []) or []:
            v = row.get(name) if isinstance(row, dict) else None
            if v is None:
                continue
            s = str(v)
            if s not in seen:
                seen.append(s)
            if len(seen) > max_distinct:
                break
        # Only useful when it's genuinely low-cardinality (an encoding, not free text).
        return f"{table}.{name}", (seen if 0 < len(seen) <= max_distinct else None)

    out: dict[str, list[str]] = {}
    workers = min(4, len(candidates))  # modest — don't spray connections at the DB
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, seen in pool.map(_probe, candidates):  # map preserves candidate order
            if seen is not None and len(out) < max_columns:
                out[key] = seen
    return out


def _remember_table_schema(orchestrator: AskOrchestrator, table: str, database: str, columns: list[ColumnInfo]) -> None:
    key = _schema_key(database, table)
    orchestrator._loop_schemas[key] = columns
    orchestrator._loop_schema_db[key] = database
    orchestrator._loop_table = table
    _note_working_db(orchestrator, database)
    orchestrator._loop_columns = columns


def _disclosed_table_names(orchestrator: AskOrchestrator) -> list[str]:
    names: list[str] = []
    for key in orchestrator._loop_schemas:
        if "." in key:
            names.append(key.split(".", 1)[1])
        else:
            names.append(key)
    return names


def _find_schema_columns(orchestrator: AskOrchestrator, table: str, database: str) -> list[ColumnInfo] | None:
    key = _schema_key(database, table)
    if key in orchestrator._loop_schemas:
        return orchestrator._loop_schemas[key]
    for schema_key, columns in orchestrator._loop_schemas.items():
        if schema_key == table or schema_key.endswith(f".{table}"):
            return columns
    return None


def _expand_to_full_columns(
    orchestrator: AskOrchestrator,
    disclosed: list[tuple[str, str, list[ColumnInfo]]],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    """Return the disclosed tables with their FULL column lists. The resolved schema
    carries only the minimal-necessary columns; clarification needs to see every
    column of the relevant tables so it grounds 'which column?' questions in the real
    fields instead of guessing. Falls back to the given columns if a describe fails."""
    full: list[tuple[str, str, list[ColumnInfo]]] = []
    for db, table, columns in disclosed:
        cols = _find_schema_columns(orchestrator, table, db)
        if not cols:
            try:
                cols = orchestrator.schema.describe_table(table, database=db)
                _remember_table_schema(orchestrator, table, db, cols)
            except Exception:  # noqa: BLE001 — grounding is best-effort, never fatal
                cols = None
        # Prefer whichever list has more columns (the full one), never fewer.
        if cols and len(cols) >= len(columns):
            full.append((db, table, cols))
        else:
            full.append((db, table, columns))
    return full


def _collect_disclosed_schemas(
    orchestrator: AskOrchestrator,
    args: dict[str, Any],
) -> list[tuple[str, str, list[ColumnInfo]]]:
    database_default = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
    tables_arg = args.get("tables")
    selected: list[tuple[str, str, list[ColumnInfo]]] = []

    if isinstance(tables_arg, list) and tables_arg:
        for raw in tables_arg:
            name = str(raw).strip()
            if not name:
                continue
            db = database_default
            columns = _find_schema_columns(orchestrator, name, db)
            if columns is None:
                columns = orchestrator.schema.describe_table(name, database=db)
                _remember_table_schema(orchestrator, name, db, columns)
            selected.append((db, name, columns))
        return selected

    if orchestrator._loop_schemas:
        for key, columns in orchestrator._loop_schemas.items():
            db = orchestrator._loop_schema_db.get(key, database_default)
            table = key.split(".", 1)[1] if "." in key else key
            selected.append((db, table, columns))
        return selected

    table = str(args.get("table") or orchestrator._loop_table or "").strip()
    if not table:
        return []
    db = str(args.get("database") or orchestrator._loop_table_database or database_default)
    columns = _find_schema_columns(orchestrator, table, db)
    if columns is None:
        columns = orchestrator.schema.describe_table(table, database=db)
        _remember_table_schema(orchestrator, table, db, columns)
    return [(db, table, columns)]


def _err(stage: str, message: str, *, retryable: bool = False) -> DBAideError:
    return DBAideError(
        code=ErrorCode.VALIDATION_FAILED,
        stage=stage,
        message=message,
        retryable=retryable,
    )


def _tables_in_sql(sql: str) -> list[str]:
    tokens = sql.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]')
            if table and table.lower() not in {"select", "where"} and table not in tables:
                tables.append(table)
    return tables
