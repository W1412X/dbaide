"""Clarification, SQL generation, validation and execution tools."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.core.result import ExecutionPolicy
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    CLARIFY_SEMANTICS, GENERATE_SQL, VALIDATE_SQL,
    EXECUTE_SQL, EXECUTE_READONLY_SQL, EXPLAIN_SQL,
)
from dbaide.agent.progress_events import subagent_event
from dbaide.agent.schema_context import (
    apply_column_notes, collect_relations, join_confidence_for_sql,
    merge_sql_context, object_notes_for_tables, validation_feedback,
)
from dbaide.agent.toolkit.support import (
    _collect_disclosed_schemas, _err, _expand_to_full_columns,
    _note_working_db, _sample_observed_values, _tables_in_sql,
)

logger = logging.getLogger("dbaide.agent.toolkit")


def register(registry: ToolRegistry, orchestrator) -> None:
    def _clarify_semantics(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.clarify import SemanticClarifier

        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        resolved = orchestrator.run_state.resolved_schema
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
                already_confirmed=list(orchestrator.run_state.clarifications),
                object_notes=object_notes,
            )
        except Exception as exc:  # noqa: BLE001 — surface the failure, do NOT fake "clear"
            logger.debug("clarify_semantics failed: %s", exc, exc_info=True)
            # Returning clear=True here would turn "never guess" into "skip clarification
            # and generate SQL anyway". Report the failure so the loop can retry/decide.
            return ToolResult(ok=False, error=_err("clarify_semantics", f"clarification failed: {exc}", retryable=True))
        # Assumptions are applied whether or not we ask, so SQL generation honours them.
        if plan.assumptions:
            orchestrator.run_state.clarifications.extend(plan.assumptions)
        if plan.is_empty():
            return ToolResult(ok=True, data={"clear": True, "assumptions": plan.assumptions})
        # Material ambiguity → pause and confirm the exact criteria with the user.
        rendered = plan.render_question()
        orchestrator.run_state.clarify_questions = rendered
        orchestrator.run_state.pending_question = rendered
        orchestrator.run_state.pending_options = plan.first_options()
        # Structured per-question list so the UI can step through them one at a time.
        orchestrator.run_state.pending_questions = [
            {"ask": str(q.get("ask") or ""), "options": list(q.get("options") or [])}
            for q in plan.questions
        ]
        return ToolResult(ok=True, data={
            "pending": True, "question": rendered, "options": plan.first_options(),
        })

    def _generate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        # Prefer the minimal-necessary schema from resolve_schema: generating SQL on
        # only the relevant tables/columns is more accurate than the full disclosure
        # (the irrelevant schema is noise). Fall back to the full disclosure if the
        # model named specific tables in args, or no resolved schema exists.
        resolved = orchestrator.run_state.resolved_schema
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
            relations = list(orchestrator.run_state.relations or [])
            if not relations and len(targets) >= 2:
                relations = collect_relations(
                    orchestrator,
                    targets,
                    question=question,
                    disclosed_schemas=disclosed,
                    parent=orchestrator.run_state.trace_node,
                )
                orchestrator.run_state.relations = relations
            ctx = merge_sql_context(orchestrator.session.disclosure.summary(), relations)
            if orchestrator.run_state.clarifications:
                ctx["criteria"] = list(orchestrator.run_state.clarifications)  # confirmed 口径
            object_notes = object_notes_for_tables(orchestrator, targets)
            if object_notes:
                ctx["object_notes"] = object_notes  # authoritative db/table user notes
            # Carry the linker's per-table rationale so the SQL writer honours WHY each
            # table/column was chosen (e.g. "picked orders_v2 because orders is deprecated").
            if resolved is not None and not resolved.is_empty():
                reasons = [
                    {"table": t.get("table", ""), "reason": t.get("reason", "")}
                    for t in resolved.tables if str(t.get("reason") or "").strip()
                ]
                if reasons:
                    ctx["schema_reasons"] = reasons
            feedback = orchestrator.run_state.sql_feedback
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
            orchestrator.run_state.sql_feedback = ""
            orchestrator.run_state.sql = draft.sql
            orchestrator.run_state.sql_rationale = draft.rationale
            orchestrator.run_state.sql_confidence = draft.confidence
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
                orchestrator.run_state.table = tables_used[0]
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
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        if not sql:
            return ToolResult(ok=False, error=_err("validate_sql", "sql is required"))
        report = orchestrator.query.validate_sql_report(sql, add_limit=True)
        if report.ok:
            orchestrator.run_state.sql = report.normalized_sql
        else:
            orchestrator.run_state.sql_feedback = validation_feedback(report.issues)
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
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("execute_sql", "sql is required"))

        policy = ExecutionPolicy(ctx.execution_policy) if ctx.execution_policy else orchestrator.execution_policy
        if policy in {ExecutionPolicy.INSPECT_ONLY, ExecutionPolicy.SQL_ONLY}:
            return ToolResult(
                ok=False,
                data={"blocked": True, "reason": f"Execution blocked by policy: {policy.value}"},
            )
        if not orchestrator.run_state.execute_allowed:
            return ToolResult(ok=False, data={"blocked": True, "reason": "Execution disabled for this request"})

        validation = orchestrator.query.validate_sql(sql, add_limit=True)
        if not validation.ok:
            issues = "; ".join(i.message for i in validation.issues)
            return ToolResult(ok=False, error=_err("execute_sql", f"SQL invalid: {issues}"))

        validation_report = orchestrator.query.validate_sql_report(validation.normalized_sql, add_limit=False)
        # Do NOT coerce a genuine 0.0 ("no confidence") up to 0.7 — `or 0.7` would mask
        # exactly the low-confidence plans the risk gate must catch. Only a missing
        # (None) confidence falls back to the neutral default.
        _conf = orchestrator.run_state.sql_confidence
        confidence = 0.7 if _conf is None else float(_conf)
        has_joins = " join " in validation.normalized_sql.lower()
        join_conf = (
            join_confidence_for_sql(orchestrator.run_state.relations, validation.normalized_sql)
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
            orchestrator.run_state.query_result = result
            orchestrator.run_state.sql = validation.normalized_sql
            orchestrator.run_state.sql_feedback = ""
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
            orchestrator.run_state.sql_feedback = str(exc)
            return ToolResult(ok=False, error=_err("execute_sql", str(exc), retryable=True))

    def _explain_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("explain_sql", "sql is required"))
        report = orchestrator.diagnose.diagnose_sql(sql, database=database)
        # Surface the diagnosis as the loop answer so it is not lost if the model
        # finishes without restating it (mirrors profile_table's behaviour).
        hints = report.get("issues") or report.get("hints") or []
        if hints:
            lines = [f"EXPLAIN diagnosis for:\n```sql\n{sql}\n```", ""]
            lines += [f"- {hint}" for hint in hints]
            orchestrator.run_state.answer = "\n".join(lines)
        return ToolResult(ok=bool(report.get("ok")), data=report)

    registry.register(CLARIFY_SEMANTICS, _clarify_semantics)
    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_READONLY_SQL, _execute_sql)
    registry.register(EXECUTE_SQL, _execute_sql)
    registry.register(EXPLAIN_SQL, _explain_sql)
