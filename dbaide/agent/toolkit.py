"""Tool handlers wired to AskOrchestrator services."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.core.result import ExecutionPolicy
from dbaide.models import ColumnInfo
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.agent.schema_context import collect_relations, merge_sql_context, validation_feedback
from dbaide.tools.specs import (
    DESCRIBE_TABLE,
    DISCOVER_SCHEMA,
    EXECUTE_READONLY_SQL,
    EXECUTE_SQL,
    EXPLAIN_SQL,
    GENERATE_SQL,
    GET_RELATIONS,
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
        _remember_table_schema(orchestrator, table, database, columns)
        payload = [
            {
                "name": c.name,
                "data_type": c.data_type,
                "primary_key": c.primary_key,
                "comment": (c.comment or "")[:120],
            }
            for c in columns
        ]
        return ToolResult(
            ok=True,
            data={
                "table": table,
                "database": database,
                "columns": payload,
                "disclosed_tables": _disclosed_table_names(orchestrator),
            },
        )

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
        relations = collect_relations(orchestrator, targets)
        orchestrator._loop_relations = relations
        return ToolResult(ok=True, data={"relations": relations, "count": len(relations)})

    def _generate_sql(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or orchestrator._loop_question or "").strip()
        disclosed = _collect_disclosed_schemas(orchestrator, args)
        if not disclosed:
            return ToolResult(ok=False, error=_err("generate_sql", "table is required (describe_table first)"))
        try:
            targets = [(db, table) for db, table, _ in disclosed]
            relations = orchestrator._loop_relations or collect_relations(orchestrator, targets)
            ctx = merge_sql_context(orchestrator.session.disclosure.summary(), relations)
            feedback = orchestrator._loop_sql_feedback
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
            tables_used = [t for _, t, _ in disclosed]
            if tables_used:
                orchestrator._loop_table = tables_used[0]
                orchestrator._loop_table_database = disclosed[0][0]
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
    registry.register(GET_RELATIONS, _get_relations)
    registry.register(GENERATE_SQL, _generate_sql)
    registry.register(VALIDATE_SQL, _validate_sql)
    registry.register(EXECUTE_READONLY_SQL, _execute_sql)
    registry.register(EXECUTE_SQL, _execute_sql)
    registry.register(EXPLAIN_SQL, _explain_sql)
    registry.register(PROFILE_TABLE, _profile_table)
    return registry


def _schema_key(database: str, table: str) -> str:
    db = database.strip()
    return f"{db}.{table}" if db else table


def _remember_table_schema(orchestrator: AskOrchestrator, table: str, database: str, columns: list[ColumnInfo]) -> None:
    key = _schema_key(database, table)
    orchestrator._loop_schemas[key] = columns
    orchestrator._loop_schema_db[key] = database
    orchestrator._loop_table = table
    orchestrator._loop_table_database = database
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
