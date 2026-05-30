"""Tool handlers wired to AskOrchestrator services."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.core.result import ExecutionPolicy, ValidationReport
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    DESCRIBE_TABLE,
    DISCOVER_SCHEMA,
    EXECUTE_READONLY_SQL,
    EXECUTE_SQL,
    EXPLAIN_SQL,
    GENERATE_SQL,
    LIST_DATABASES,
    LIST_TABLES,
    PROFILE_TABLE,
    SYNTHESIZE_SCHEMA_ANSWER,
    VALIDATE_SQL,
)

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.toolkit")


def build_tool_registry(orchestrator: AskOrchestrator) -> ToolRegistry:
    """Register all agent tools bound to an orchestrator instance."""
    registry = ToolRegistry()

    def _discover_schema(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("discover_schema", "question is required"))
        try:
            discovery = orchestrator._discover(question)
            orchestrator._loop_discovery = discovery
            hits = [
                {"kind": h.kind, "path": h.path, "name": h.name, "database": h.database, "summary": h.summary[:240]}
                for h in discovery.hits
            ]
            return ToolResult(ok=True, data={"hits": hits, "trace": discovery.trace, "count": len(hits)})
        except Exception as exc:
            logger.warning("discover_schema_failed: %s", exc)
            return ToolResult(ok=False, error=_err("discover_schema", str(exc), retryable=True))

    def _synthesize_schema_answer(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.progressive_schema import ProgressiveSchemaAgent

        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", "question is required"))
        try:
            discovery = orchestrator._loop_discovery or orchestrator._discover(question)
            agent = ProgressiveSchemaAgent(orchestrator.llm, orchestrator.asset_store, orchestrator.instance)
            answer = agent.synthesize_answer(question, discovery)
            orchestrator._loop_answer = answer
            return ToolResult(ok=True, data={"answer": answer})
        except Exception as exc:
            return ToolResult(ok=False, error=_err("synthesize_schema_answer", str(exc), retryable=True))

    def _list_databases(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        dbs = orchestrator.schema.list_databases()
        return ToolResult(ok=True, data={"databases": dbs})

    def _list_tables(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database = str(args.get("database") or orchestrator._loop_database or "")
        tables = orchestrator.schema.list_tables(database=database)
        payload = [{"name": t.name, "comment": (t.comment or "")[:120]} for t in tables[:50]]
        return ToolResult(ok=True, data={"database": database, "tables": payload})

    def _describe_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or "").strip()
        database = str(args.get("database") or orchestrator._loop_database or "")
        if not table:
            return ToolResult(ok=False, error=_err("describe_table", "table is required"))
        columns = orchestrator.schema.describe_table(table, database=database)
        orchestrator._loop_table = table
        orchestrator._loop_table_database = database
        orchestrator._loop_columns = columns
        payload = [
            {
                "name": c.name,
                "data_type": c.data_type,
                "primary_key": c.primary_key,
                "comment": (c.comment or "")[:120],
            }
            for c in columns
        ]
        return ToolResult(ok=True, data={"table": table, "database": database, "columns": payload})

    def _generate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        table = str(args.get("table") or orchestrator._loop_table or "").strip()
        database = str(args.get("database") or orchestrator._loop_table_database or orchestrator._loop_database or "")
        if not table:
            return ToolResult(ok=False, error=_err("generate_sql", "table is required"))
        columns = orchestrator._loop_columns
        if not columns:
            columns = orchestrator.schema.describe_table(table, database=database)
            orchestrator._loop_columns = columns
        try:
            draft = orchestrator.sql_writer.write(
                question, table, columns, context=orchestrator.session.disclosure.summary(),
            )
            orchestrator._loop_sql = draft.sql
            orchestrator._loop_sql_rationale = draft.rationale
            orchestrator._loop_sql_confidence = draft.confidence
            return ToolResult(
                ok=True,
                data={"sql": draft.sql, "rationale": draft.rationale, "confidence": draft.confidence},
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("generate_sql", str(exc), retryable=True))

    def _validate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator._loop_sql or "").strip()
        if not sql:
            return ToolResult(ok=False, error=_err("validate_sql", "sql is required"))
        validation = orchestrator.query.validate_sql(sql, add_limit=True)
        if validation.ok:
            orchestrator._loop_sql = validation.normalized_sql
        issues = [{"message": i.message, "severity": i.severity} for i in validation.issues]
        return ToolResult(
            ok=validation.ok,
            data={
                "ok": validation.ok,
                "normalized_sql": validation.normalized_sql,
                "issues": issues,
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

        validation_report = ValidationReport(
            ok=True,
            normalized_sql=validation.normalized_sql,
            issues=[],
            risk_level="low",
        )
        confidence = float(orchestrator._loop_sql_confidence or 0.7)
        risk = orchestrator.risk.decide(
            policy=policy,
            validation=validation_report,
            plan_confidence=confidence,
            table_count=max(1, len(_tables_in_sql(validation.normalized_sql))),
            has_joins=" join " in validation.normalized_sql.lower(),
        )
        if risk.action != "auto_execute":
            return ToolResult(
                ok=False,
                data={
                    "blocked": True,
                    "reason": risk.reason,
                    "risk_action": risk.action,
                    "sql": validation.normalized_sql,
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
            return ToolResult(ok=False, error=_err("execute_sql", str(exc), retryable=True))

    def _explain_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        sql = str(args.get("sql") or orchestrator._loop_sql or "").strip()
        database = str(args.get("database") or orchestrator._loop_database or "")
        if not sql:
            return ToolResult(ok=False, error=_err("explain_sql", "sql is required"))
        report = orchestrator.diagnose.diagnose_sql(sql, database=database)
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

    registry.register(DISCOVER_SCHEMA, _discover_schema)
    registry.register(SYNTHESIZE_SCHEMA_ANSWER, _synthesize_schema_answer)
    registry.register(LIST_DATABASES, _list_databases)
    registry.register(LIST_TABLES, _list_tables)
    registry.register(DESCRIBE_TABLE, _describe_table)
    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_READONLY_SQL, _execute_sql)
    registry.register(EXECUTE_SQL, _execute_sql)
    registry.register(EXPLAIN_SQL, _explain_sql)
    registry.register(PROFILE_TABLE, _profile_table)
    return registry


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
