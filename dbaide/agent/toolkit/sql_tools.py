"""Clarification, SQL generation, validation and execution tools."""
from __future__ import annotations

import hashlib
import inspect
import logging
from typing import Any

from dbaide.agent.memory import SQLArtifact, next_prefixed_id
from dbaide.agent.sql_executions import normalize_sql_purpose, record_sql_execution
from dbaide.i18n import t
from dbaide.agent.optimizer_agent import OptimizerAgent
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    GENERATE_SQL, VALIDATE_SQL,
    EXECUTE_SQL, EXPLAIN_SQL,
)
from dbaide.agent.progress_events import subagent_event
from dbaide.agent.schema_context import (
    apply_column_notes, index_context_for_schemas, join_confidence_for_sql,
    merge_sql_context, object_notes_for_tables, validation_feedback,
)
from dbaide.agent.toolkit.support import (
    _collect_disclosed_schemas, _err,
    _note_working_db, _safe_float, _tables_in_sql,
    _requested_table_names, _ambiguous_requested_tables,
    _requested_table_labels,
)
from dbaide.agent.toolkit.result_preview import preview_rows

logger = logging.getLogger("dbaide.agent.toolkit")


def _can_fast_execute(orchestrator, draft, disclosed) -> bool:
    """Check whether a freshly generated SQL qualifies for the fast path."""
    if not orchestrator.run_state.execute_allowed:
        return False
    if len(disclosed) > 1:
        return False
    if draft.confidence is not None and draft.confidence < 0.8:
        return False
    # In conversation-stream architecture, the model's own context serves as the
    # "open questions" tracker. If it calls generate_sql with high confidence,
    # we trust its judgment that no questions remain.
    return True


def _try_fast_execute(orchestrator, draft, disclosed, database) -> ToolResult | None:
    """Attempt validate → risk-check → execute inline.  Returns None to fall back."""
    report = orchestrator.query.validate_sql_report(draft.sql, add_limit=True)
    if not report.ok:
        orchestrator.run_state.sql_feedback = validation_feedback(report.issues)
        return None

    orchestrator.progress(subagent_event(
        agent="validate",
        title="Fast validate passed",
        parent="generate_sql",
        detail=report.normalized_sql[:160],
        status="completed",
    ))

    risk = orchestrator.risk.decide(
        validation=report,
        plan_confidence=draft.confidence if draft.confidence is not None else 0.8,
        table_count=1,
        has_joins=False,
        join_confidence=1.0,
    )
    if risk.action != "auto_execute":
        return None

    orchestrator.run_state.sql = report.normalized_sql
    orchestrator.run_state.query_result = None
    limit = orchestrator.session.default_limit
    timeout_seconds = orchestrator.session.timeout_seconds

    try:
        execute_kwargs: dict[str, Any] = {"database": database, "limit": limit}
        if timeout_seconds is not None:
            execute_kwargs["timeout_seconds"] = timeout_seconds
        if "confirmed" in inspect.signature(orchestrator.query.execute_sql).parameters:
            execute_kwargs["confirmed"] = True
        result = orchestrator.query.execute_sql(report.normalized_sql, **execute_kwargs)
    except Exception as exc:
        logger.debug("fast_execute_fallback: %s", exc)
        return None

    orchestrator.run_state.query_result = result
    orchestrator.run_state.sql = report.normalized_sql
    orchestrator.run_state.sql_feedback = ""
    artifact_id = _next_sql_artifact_id(orchestrator.run_state.memory)
    result_summary = _result_summary(result)
    rows_preview, preview_meta = preview_rows(
        list(result.rows or []),
        columns=list(result.columns or []),
        max_rows=20,
    )
    purpose = normalize_sql_purpose("")
    orchestrator.run_state.memory.add_sql_artifact(SQLArtifact(
        id=artifact_id,
        purpose=purpose,
        sql=report.normalized_sql,
        database=database,
        row_count=int(result.row_count or 0),
        columns=list(result.columns or []),
        rows_preview=list((result.rows or [])[:10]),
        result_summary=result_summary,
        warnings=list(report.warnings or []),
        truncated=bool(getattr(result, "truncated", False)),
    ))
    record_sql_execution(
        orchestrator.run_state,
        sql=report.normalized_sql,
        purpose=purpose,
        database=database,
        tool="execute_sql",
        row_count=int(result.row_count or 0),
        elapsed_ms=float(result.elapsed_ms or 0.0),
        artifact_id=artifact_id,
        columns=list(result.columns or []),
    )
    orchestrator.progress(subagent_event(
        agent="sql",
        title=f"Fast executed · {result.row_count} rows · {result.elapsed_ms:.0f}ms",
        parent="generate_sql",
        detail=report.normalized_sql[:160],
        status="completed",
    ))
    return ToolResult(
        ok=True,
        data={
            "sql": report.normalized_sql,
            "fast_executed": True,
            "rationale": draft.rationale,
            "confidence": draft.confidence,
            "columns": result.columns,
            "rows": rows_preview,
            "row_preview": preview_meta,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
            "artifact_id": artifact_id,
            "result_summary": result_summary,
            "database": database,
            "tables": [t for _, t, _ in disclosed],
        },
    )


def register(registry: ToolRegistry, orchestrator) -> None:
    def _generate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator.run_state.question or "").strip()
        # Generate from the schemas the main brain has explicitly disclosed or named.
        # retrieve_schema_context is evidence-only; it does not produce a final schema
        # selection. If several candidates were recalled, the LLM should pass `tables`
        # for the ones it intentionally chose, ask_user, or inspect more evidence.
        requested_tables = _requested_table_names(args)
        ambiguous_tables = _ambiguous_requested_tables(orchestrator, args)
        if ambiguous_tables:
            return ToolResult(
                ok=False,
                error=_err(
                    "generate_sql",
                    "ambiguous requested table(s); specify database.table or database: "
                    + ", ".join(f"{name} -> {', '.join(labels)}" for name, labels in ambiguous_tables.items()),
                    retryable=True,
                ),
                data={"ambiguous_tables": ambiguous_tables},
            )
        disclosed = _collect_disclosed_schemas(orchestrator, args)
        if requested_tables and len(disclosed) != len(requested_tables):
            found = {
                f"{db}.{table}" if db else table
                for db, table, _columns in disclosed
            }
            requested_labels = _requested_table_labels(orchestrator, args)
            missing = [
                raw for raw, label in zip(requested_tables, requested_labels, strict=False)
                if label not in found
            ]
            if missing:
                return ToolResult(
                    ok=False,
                    error=_err(
                        "generate_sql",
                        "requested table(s) not found or not readable: " + ", ".join(missing),
                        retryable=True,
                    ),
                    data={"requested_tables": requested_tables, "found_tables": sorted(found), "missing_tables": missing},
                )
        if not disclosed:
            return ToolResult(ok=False, error=_err("generate_sql", "table is required (retrieve_schema_context or describe_table first)"))
        # Backfill user column notes regardless of how `disclosed` was built (the
        # direct table selection can bypass _disclosed_schemas_for_tables).
        apply_column_notes(orchestrator, disclosed)
        try:
            targets = [(db, table) for db, table, _ in disclosed]
            relations = list(orchestrator.run_state.relations or [])
            # Scope the SQL-writer schema context to THIS call's target tables.
            # The disclosure summary may hold every table discovered this turn;
            # feeding all of them would add token noise and dilute table selection.
            # Target columns themselves come from `disclosed`, so filtering is safe.
            target_refs = {(str(db or ""), str(table)) for db, table, _ in disclosed}
            summary = orchestrator.session.disclosure.summary()
            summary["tables"] = [
                t for t in (summary.get("tables") or [])
                if (str(t.get("database") or ""), str(t.get("name") or "")) in target_refs
            ]
            ctx = merge_sql_context(summary, relations)
            ctx["answer_language"] = orchestrator.run_state.answer_language
            if orchestrator.run_state.clarifications:
                ctx["criteria"] = list(orchestrator.run_state.clarifications)  # confirmed 口径
            index_context = index_context_for_schemas(orchestrator, disclosed)
            if index_context:
                ctx["indexes"] = index_context
            object_notes = object_notes_for_tables(orchestrator, _sql_note_targets(orchestrator, targets))
            if object_notes:
                ctx["object_notes"] = object_notes  # authoritative db/table user notes
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
            orchestrator.run_state.query_result = None
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
            if _can_fast_execute(orchestrator, draft, disclosed):
                fast = _try_fast_execute(
                    orchestrator, draft, disclosed,
                    database=disclosed[0][0] or orchestrator.run_state.table_database or orchestrator.run_state.database,
                )
                if fast is not None:
                    return fast
                orchestrator.run_state.sql_feedback = ""
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
        previous_sql = str(orchestrator.run_state.sql or "").strip()
        report = orchestrator.query.validate_sql_report(sql, add_limit=True)
        if report.ok:
            orchestrator.run_state.sql = report.normalized_sql
            if _sql_hash(report.normalized_sql) != _sql_hash(previous_sql):
                orchestrator.run_state.query_result = None
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
        exploratory = bool(args.get("exploratory", False))
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        purpose = str(args.get("purpose") or "").strip()
        save_as = str(args.get("save_as") or "").strip()
        limit = _positive_int(args.get("limit"), orchestrator.session.default_limit)
        timeout_seconds = _positive_int(args.get("timeout_seconds"), None)
        if not sql:
            return ToolResult(ok=False, error=_err("execute_sql", "sql is required"))

        if not orchestrator.run_state.execute_allowed:
            return ToolResult(
                ok=False,
                error=_err("execute_sql", "Execution disabled for this request", retryable=False),
            )

        validation = orchestrator.query.validate_sql(sql, add_limit=True, limit=limit)
        if not validation.ok:
            issues = "; ".join(i.message for i in validation.issues)
            orchestrator.run_state.sql_feedback = validation_feedback([i.message for i in validation.issues])
            return ToolResult(ok=False, error=_err("execute_sql", f"SQL invalid: {issues}"))
        orchestrator.run_state.query_result = None

        validation_report = orchestrator.query.validate_sql_report(
            validation.normalized_sql,
            add_limit=False,
            limit=limit,
        )
        # Do NOT coerce a genuine 0.0 ("no confidence") up to 0.7 — `or 0.7` would mask
        # exactly the low-confidence plans the risk gate must catch. Only a missing
        # (None) confidence falls back to the neutral default.
        _conf = orchestrator.run_state.sql_confidence
        confidence = 0.7 if _conf is None else _safe_float(_conf, 0.7)
        table_count = max(1, len(_tables_in_sql(validation.normalized_sql)))
        has_joins = table_count > 1
        join_conf = (
            join_confidence_for_sql(orchestrator.run_state.relations, validation.normalized_sql)
            if has_joins
            else 1.0
        )
        # Pre-execution cost gate: estimate the scan size via EXPLAIN. Also drives the
        # advisory optimizer below, so estimate when EITHER threshold is configured.
        explain_max_rows = getattr(orchestrator.query, "explain_max_rows", 0)
        optimize_advise_rows = getattr(orchestrator.query, "optimize_advise_rows", 0)
        estimated_rows = None
        if explain_max_rows or optimize_advise_rows:
            estimated_rows = orchestrator.query.estimate_rows(validation.normalized_sql, database=database)
            if estimated_rows is not None and explain_max_rows:
                orchestrator.progress(
                    subagent_event(
                        agent="explain",
                        title=f"EXPLAIN ~{estimated_rows:,} rows",
                        parent="execute_sql",
                        detail=f"cost gate limit {explain_max_rows:,}",
                        status="completed" if estimated_rows <= explain_max_rows else "info",
                    ),
                )
        # SQL optimizer (advisory; single LLM call over SQL + EXPLAIN plan + relevant schema).
        # Two modes for a query whose estimated scan exceeds the advise threshold:
        #  - "gate": advise BEFORE executing and return the suggestions so the agent can
        #     rewrite. A one-shot run_state flag exempts the very next execute_sql (the
        #     rewrite OR the same SQL resubmitted) → exactly one advise, never a loop.
        #  - "suggest": run the query, then attach the suggestions to the result.
        optimize_mode = getattr(orchestrator.query, "optimize_advise_mode", "suggest")
        heavy = bool(optimize_advise_rows and estimated_rows is not None
                     and estimated_rows > optimize_advise_rows)
        # A prior gate armed a ONE-SHOT exemption. The first execute_sql after it (the agent's
        # rewrite OR the same SQL resubmitted) runs un-advised whatever its cost — consume the
        # flag UNCONDITIONALLY here so it can never leak to a later unrelated heavy query (e.g.
        # when the rewrite turned out cheap and skipped the block below).
        was_gated = bool(orchestrator.run_state.skip_next_optimize)
        orchestrator.run_state.skip_next_optimize = False
        optimization = None
        if optimize_mode != "off" and heavy and not (optimize_mode == "gate" and was_gated):
            try:
                optimization = OptimizerAgent(orchestrator.llm).evaluate_sql(
                    validation.normalized_sql, query_tools=orchestrator.query, database=database,
                    language=orchestrator.run_state.answer_language)
            except Exception:  # noqa: BLE001 - advisory: never fail the query over advice
                optimization = None
            if optimization:
                orchestrator.progress(subagent_event(
                    agent="optimize",
                    title="Optimization suggestions" if optimize_mode == "suggest" else "Optimization gate",
                    parent="execute_sql", detail=optimization, status="info"))
                if optimize_mode == "gate":
                    # advise before executing; exempt the next call so it can't loop
                    orchestrator.run_state.skip_next_optimize = True
                    return ToolResult(ok=True, data={
                            "executed": False,
                            "optimization": optimization,
                            "sql": validation.normalized_sql,
                            "estimated_rows": estimated_rows,
                            "guidance": (
                                "This query is expensive and was NOT executed yet. Consider the "
                                "optimization suggestions above. Call execute_sql again — with an "
                                "improved query if you can write one, otherwise with the same SQL to "
                                "run it as-is. The next call executes directly (you won't be advised "
                                "again), so do not keep re-optimizing."
                            ),
                        })
        risk = orchestrator.risk.decide(
            validation=validation_report,
            plan_confidence=confidence,
            table_count=table_count,
            has_joins=has_joins,
            join_confidence=join_conf,
            estimated_rows=estimated_rows,
            explain_max_rows=explain_max_rows,
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
            sql_hash = _sql_hash(validation.normalized_sql)
            if sql_hash not in orchestrator.run_state.confirmed_risk_sqls:
                execute_args = {
                    "sql": validation.normalized_sql,
                    "database": database,
                    "limit": limit,
                }
                if purpose:
                    execute_args["purpose"] = purpose
                if save_as:
                    execute_args["save_as"] = save_as
                if timeout_seconds is not None:
                    execute_args["timeout_seconds"] = timeout_seconds
                question = _risk_confirmation_question(
                    sql=validation.normalized_sql,
                    reason=risk.reason,
                    warnings=validation_report.warnings,
                    estimated_rows=estimated_rows,
                )
                options = [t("risk.execute_anyway"), t("risk.cancel")]
                orchestrator.run_state.pending_question = question
                orchestrator.run_state.pending_options = options
                orchestrator.run_state.pending_questions = [{"ask": question, "options": options}]
                orchestrator.run_state.risk_confirmation = {
                    "sql": validation.normalized_sql,
                    "sql_hash": sql_hash,
                    "execute_args": execute_args,
                    "tool": "execute_sql",
                    "reason": risk.reason,
                    "risk_action": risk.action,
                    "risk_level": risk.risk_level,
                    "warnings": validation_report.warnings,
                    "estimated_rows": estimated_rows,
                }
                return ToolResult(
                    ok=True,
                    data={
                        "pending": True,
                        "question": question,
                        "options": options,
                        "reason": risk.reason,
                        "risk_action": risk.action,
                        "risk_level": risk.risk_level,
                        "sql": validation.normalized_sql,
                        "execute_args": execute_args,
                        "warnings": validation_report.warnings,
                        **({"optimization": optimization} if optimization else {}),
                    },
                )

        try:
            execute_kwargs = {
                "database": database,
                "limit": limit,
            }
            if timeout_seconds is not None:
                execute_kwargs["timeout_seconds"] = timeout_seconds
            if "confirmed" in inspect.signature(orchestrator.query.execute_sql).parameters:
                execute_kwargs["confirmed"] = True
            result = orchestrator.query.execute_sql(validation.normalized_sql, **execute_kwargs)
            if not exploratory:
                orchestrator.run_state.query_result = result
                orchestrator.run_state.sql = validation.normalized_sql
                orchestrator.run_state.sql_feedback = ""
            artifact_id = save_as or _next_sql_artifact_id(orchestrator.run_state.memory)
            result_summary = _result_summary(result)
            norm_purpose = normalize_sql_purpose(purpose)
            rows_preview, preview_meta = preview_rows(
                list(result.rows or []),
                columns=list(result.columns or []),
                max_rows=20,
            )
            orchestrator.run_state.memory.add_sql_artifact(SQLArtifact(
                id=artifact_id,
                purpose=norm_purpose,
                sql=validation.normalized_sql,
                database=database,
                row_count=int(result.row_count or 0),
                columns=list(result.columns or []),
                rows_preview=list((result.rows or [])[:10]),
                result_summary=result_summary,
                warnings=list(validation_report.warnings or []),
                truncated=bool(getattr(result, "truncated", False)),
            ))
            record_sql_execution(
                orchestrator.run_state,
                sql=validation.normalized_sql,
                purpose=norm_purpose,
                database=database,
                tool="execute_sql",
                row_count=int(result.row_count or 0),
                elapsed_ms=float(result.elapsed_ms or 0.0),
                artifact_id=artifact_id,
                columns=list(result.columns or []),
            )
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
                    "rows": rows_preview,
                    "row_preview": preview_meta,
                    "row_count": result.row_count,
                    "truncated": result.truncated,
                    "elapsed_ms": result.elapsed_ms,
                    "artifact_id": artifact_id,
                    "purpose": norm_purpose,
                    "result_summary": result_summary,
                    "sql": validation.normalized_sql,
                    "database": database,
                    **({"optimization": optimization} if optimization else {}),
                },
            )
        except PermissionError as exc:
            # Permission / auth errors are never retryable — the SQL is valid
            # but the connection lacks privileges.
            if not exploratory:
                orchestrator.run_state.sql_feedback = str(exc)
            return ToolResult(ok=False, error=_err("execute_sql", str(exc), retryable=False))
        except Exception as exc:
            timeout_feedback = _sql_timeout_feedback(
                validation.normalized_sql,
                database=database,
                exc=exc,
                timeout_seconds=timeout_seconds or orchestrator.session.timeout_seconds,
            ) if _looks_sql_timeout(exc) else ""
            if not exploratory:
                orchestrator.run_state.sql_feedback = timeout_feedback or str(exc)
            # Timeout / transient errors MAY be retryable; schema/structural
            # errors likely are not, but it's hard to classify every adapter
            # exception. Mark as retryable so the model can adjust its SQL, but
            # the circuit-breaker in the agent loop will cut off identical
            # repeated failures.
            message = timeout_feedback or str(exc)
            return ToolResult(
                ok=False,
                error=_err("execute_sql", message, retryable=True),
                data={"timeout": True, "optimization_feedback": timeout_feedback} if timeout_feedback else None,
            )

    def _explain_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("explain_sql", "sql is required"))
        report = orchestrator.diagnose.diagnose_sql(sql, database=database)
        return ToolResult(ok=bool(report.get("ok")), data=report)

    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_SQL, _execute_sql)
    registry.register(EXPLAIN_SQL, _explain_sql)


def _sql_note_targets(orchestrator, selected: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Tables whose object notes must reach SQL generation.

    The selected SQL tables are not enough: schema retrieval may have seen user
    notes on nearby candidates. Those notes are still authoritative context for
    SQL writing; the model decides what the note means.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(database: str, table: str) -> None:
        key = (str(database or "").strip(), str(table or "").strip())
        if key[1] and key not in seen:
            seen.add(key)
            out.append(key)

    for db, table in selected:
        add(db, table)

    reports = getattr(getattr(orchestrator, "run_state", None), "memory", None)
    for report in list(getattr(reports, "schema_reports", []) or [])[-3:]:
        for candidate in getattr(report, "candidates", []) or []:
            notes = getattr(candidate, "notes", {}) or {}
            if getattr(candidate, "status", "active") != "active" or notes.get("table"):
                add(getattr(candidate, "database", ""), getattr(candidate, "table", ""))
    return out


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(" ".join(str(sql or "").split()).encode("utf-8")).hexdigest()


def _next_sql_artifact_id(memory: Any) -> str:
    return next_prefixed_id(memory, "sql:", collections=("sql_artifacts",))


def _result_summary(result: Any) -> str:
    rows = list(getattr(result, "rows", []) or [])
    columns = list(getattr(result, "columns", []) or [])
    if not rows:
        return "No rows returned."
    if len(rows) == 1:
        row = rows[0]
        if isinstance(row, dict):
            parts = [f"{col}={row.get(col)!r}" for col in columns[:6]]
            return "Single row: " + ", ".join(parts)
    return f"Previewed {min(len(rows), 10)} row(s) with columns: {', '.join(columns[:8])}."


def _positive_int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _looks_sql_timeout(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    markers = (
        "timeout", "timed out", "deadline", "statement timeout",
        "max_statement_time", "query execution was interrupted",
        "lock wait timeout", "execution expired", "query_canceled",
        "canceling statement",
    )
    return any(marker in text for marker in markers)


def _sql_timeout_feedback(
    sql: str,
    *,
    database: str,
    exc: Exception,
    timeout_seconds: int | None,
) -> str:
    """Actionable feedback for the next SQL-writing iteration after a DB timeout."""
    timeout_text = f"{timeout_seconds}s" if timeout_seconds else "the configured timeout"
    lines = [
        f"SQL execution timed out after {timeout_text}: {exc}",
        "Treat this as a slow-query/query-plan problem, not as final failure. Rewrite the SQL before retrying.",
        f"Timed-out SQL: {sql.strip()[:1200]}",
        "General repair rules:",
        "- Do not simply raise timeout or retry the identical SQL.",
        "- Avoid slow queries: reduce scanned rows, push selective filters earlier, and choose cheaper join order/keys.",
        "- Use available schema and index context to write fast SQL; aggregate before joining large tables when possible.",
        "- Prefer EXISTS/key-set checks, bounded validation queries, or sampled checks for consistency questions.",
        "- Use EXPLAIN or narrower probes if the next safe rewrite is unclear.",
        "- Retry only with materially optimized SQL.",
    ]
    if database:
        lines.append(f"Database scope: {database}")
    lines.append("Do not simply raise timeout unless the user explicitly asks for a long-running export.")
    return "\n".join(lines)


def _risk_confirmation_question(
    *,
    sql: str,
    reason: str,
    warnings: list[str],
    estimated_rows: int | None,
) -> str:
    lines = [
        t("risk.confirm_title"),
        "",
        t("risk.reason", reason=reason),
    ]
    if estimated_rows is not None:
        lines.append(t("risk.estimated_rows", rows=f"{estimated_rows:,}"))
    if warnings:
        lines.append("")
        lines.append(t("risk.warnings"))
        lines.extend(f"- {w}" for w in warnings[:5])
    lines += ["", t("risk.sql"), "```sql", sql, "```"]
    return "\n".join(lines)
