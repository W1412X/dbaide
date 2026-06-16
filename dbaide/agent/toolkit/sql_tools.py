"""Clarification, SQL generation, validation and execution tools."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from typing import Any

from dbaide.agent.memory import SQLArtifact, next_prefixed_id
from dbaide.agent.sql_executions import normalize_sql_purpose, record_sql_execution
from dbaide.i18n import t
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    GENERATE_SQL, VALIDATE_SQL,
    EXECUTE_SQL, EXECUTE_READONLY_SQL, EXPLAIN_SQL,
)
from dbaide.agent.progress_events import subagent_event
from dbaide.agent.schema_context import (
    apply_column_notes, join_confidence_for_sql,
    merge_sql_context, object_notes_for_tables, validation_feedback,
)
from dbaide.agent.toolkit.support import (
    _collect_disclosed_schemas, _err,
    _note_working_db, _safe_float, _tables_in_sql,
    _requested_table_names, _ambiguous_requested_tables,
    _requested_table_labels,
)

logger = logging.getLogger("dbaide.agent.toolkit")


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
            ctx = merge_sql_context(orchestrator.session.disclosure.summary(), relations)
            ctx["answer_language"] = orchestrator.run_state.answer_language
            if orchestrator.run_state.clarifications:
                ctx["criteria"] = list(orchestrator.run_state.clarifications)  # confirmed 口径
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

    def _execute_sql(args: dict[str, Any], ctx: ToolContext, *, exploratory: bool = False) -> ToolResult:
        tool_label = "execute_readonly_sql" if exploratory else "execute_sql"
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        purpose = str(args.get("purpose") or "").strip()
        save_as = str(args.get("save_as") or "").strip()
        limit = _positive_int(args.get("limit"), orchestrator.session.default_limit)
        timeout_seconds = _positive_int(args.get("timeout_seconds"), None)
        if not sql:
            return ToolResult(ok=False, error=_err(tool_label, "sql is required"))

        if not orchestrator.run_state.execute_allowed:
            return ToolResult(
                ok=False,
                error=_err(tool_label, "Execution disabled for this request", retryable=False),
            )

        validation = orchestrator.query.validate_sql(sql, add_limit=True, limit=limit)
        if not validation.ok:
            issues = "; ".join(i.message for i in validation.issues)
            orchestrator.run_state.sql_feedback = validation_feedback([i.message for i in validation.issues])
            return ToolResult(ok=False, error=_err(tool_label, f"SQL invalid: {issues}"))
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
        # Pre-execution cost gate: estimate the scan size via EXPLAIN.
        explain_max_rows = getattr(orchestrator.query, "explain_max_rows", 0)
        estimated_rows = None
        if explain_max_rows:
            estimated_rows = orchestrator.query.estimate_rows(validation.normalized_sql, database=database)
            if estimated_rows is not None:
                orchestrator.progress(
                    subagent_event(
                        agent="explain",
                        title=f"EXPLAIN ~{estimated_rows:,} rows",
                        parent=tool_label,
                        detail=f"cost gate limit {explain_max_rows:,}",
                        status="completed" if estimated_rows <= explain_max_rows else "info",
                    ),
                )
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
                parent=tool_label,
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
                    "tool": "execute_readonly_sql" if exploratory else "execute_sql",
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
                tool=tool_label,
                row_count=int(result.row_count or 0),
                elapsed_ms=float(result.elapsed_ms or 0.0),
                artifact_id=artifact_id,
                columns=list(result.columns or []),
            )
            orchestrator.progress(
                subagent_event(
                    agent="sql",
                    title=f"Executed · {result.row_count} rows · {result.elapsed_ms:.0f}ms",
                    parent=tool_label,
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
                    "artifact_id": artifact_id,
                    "purpose": norm_purpose,
                    "result_summary": result_summary,
                    "sql": validation.normalized_sql,
                    "database": database,
                },
            )
        except PermissionError as exc:
            # Permission / auth errors are never retryable — the SQL is valid
            # but the connection lacks privileges.
            if not exploratory:
                orchestrator.run_state.sql_feedback = str(exc)
            return ToolResult(ok=False, error=_err(tool_label, str(exc), retryable=False))
        except Exception as exc:
            if not exploratory:
                orchestrator.run_state.sql_feedback = str(exc)
            # Timeout / transient errors MAY be retryable; schema/structural
            # errors likely are not, but it's hard to classify every adapter
            # exception. Mark as retryable so the model can adjust its SQL, but
            # the circuit-breaker in the agent loop will cut off identical
            # repeated failures.
            return ToolResult(ok=False, error=_err(tool_label, str(exc), retryable=True))

    def _explain_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator.run_state.sql or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("explain_sql", "sql is required"))
        report = orchestrator.diagnose.diagnose_sql(sql, database=database)
        if report.get("ok"):
            explain = report.get("explain") or []
            if orchestrator.run_state.answer_language == "zh":
                lines = [f"EXPLAIN 诊断：\n```sql\n{sql}\n```", ""]
            else:
                lines = [f"EXPLAIN diagnosis for:\n```sql\n{sql}\n```", ""]
            lines.append("```text")
            lines.append(json.dumps(explain, ensure_ascii=False, default=str, indent=2))
            lines.append("```")
            orchestrator.run_state.answer = "\n".join(lines)
        elif report.get("issues"):
            if orchestrator.run_state.answer_language == "zh":
                lines = [f"EXPLAIN 诊断：\n```sql\n{sql}\n```", ""]
            else:
                lines = [f"EXPLAIN diagnosis for:\n```sql\n{sql}\n```", ""]
            lines += [f"- {issue}" for issue in report.get("issues") or []]
            orchestrator.run_state.answer = "\n".join(lines)
        return ToolResult(ok=bool(report.get("ok")), data=report)

    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_READONLY_SQL, lambda args, ctx: _execute_sql(args, ctx, exploratory=True))
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
