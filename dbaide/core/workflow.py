"""Workflow engine that wraps existing DataAssistant with trace and structured results."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

from dbaide.adapters import build_adapter
from dbaide.agent.progress_events import progress_label
from dbaide.agent import DataAssistant
from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.result import (
    ExecutionPolicy,
    NextAction,
    QueryPlan,
    SQLCandidate,
    ValidationReport,
    WorkflowRequest,
    WorkflowResult,
    WorkflowStatus,
)
from dbaide.session import Session
from dbaide.assets import AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig
from dbaide.tools import QueryTools

logger = logging.getLogger("dbaide.workflow")


class WorkflowEngine:
    """Unified workflow engine consumed by CLI and GUI.

    Wraps existing DataAssistant with structured results and trace.
    """

    def __init__(
        self,
        connection: ConnectionConfig,
        llm: LLMClient | None = None,
        asset_store: AssetStore | None = None,
        join_catalog: JoinCatalogStore | None = None,
    ) -> None:
        self.connection = connection
        self.llm = llm
        self.asset_store = asset_store or AssetStore()
        self.join_catalog = join_catalog or JoinCatalogStore()
        self._adapter = None
        self._session = None

    def run(
        self,
        request: WorkflowRequest,
        *,
        progress: Callable[[str], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> WorkflowResult:
        """Execute a workflow synchronously."""
        result = WorkflowResult(
            question=request.question,
            connection_name=request.connection_name or self.connection.name,
            database_scope=request.database_scope,
            execution_policy=request.execution_policy,
            created_at=time.time(),
        )

        result.status = WorkflowStatus.RUNNING
        self._trace(result, "workflow_started", "Workflow started", "agent", summary=request.question)

        self._trace(result, "environment_check", "Checking environment", "system")
        try:
            adapter = self._get_adapter()
            adapter.test()
        except Exception as exc:
            result.status = WorkflowStatus.FAILED
            result.errors.append(DBAideError(
                code=ErrorCode.CONNECTION_FAILED,
                stage="check_environment",
                message=f"Connection failed: {exc}",
                hint="Check connection settings and database availability",
                retryable=True,
                repair_action=RepairAction.REFRESH_SCHEMA,
            ))
            result.completed_at = time.time()
            self._trace(result, "workflow_failed", "Workflow failed", "system", level=TraceLevel.ERROR, summary=str(exc))
            return result

        database = request.database_scope[0] if request.database_scope else ""
        execute = request.execution_policy not in {ExecutionPolicy.INSPECT_ONLY, ExecutionPolicy.SQL_ONLY}
        if request.execution_policy == ExecutionPolicy.INSPECT_ONLY:
            self._trace(result, "context_collection", "Reading assets only", "agent")
        elif request.execution_policy == ExecutionPolicy.SQL_ONLY:
            self._trace(result, "planning", "Planning SQL without execution", "agent")
        else:
            self._trace(result, "planning", "Planning guarded read-only workflow", "agent")

        assistant = self._build_assistant(request)
        self._trace(result, "agent_request", "Running assistant", "agent")

        def on_progress(msg: str | dict[str, Any]) -> None:
            if cancel_check:
                cancel_check()
            label = progress_label(msg)
            if isinstance(msg, dict):
                self._trace(
                    result,
                    str(msg.get("stage") or "agent_progress"),
                    label,
                    str(msg.get("kind") or "agent"),
                    summary=str(msg.get("detail") or label)[:120],
                )
            else:
                self._trace(result, "agent_progress", msg, "agent", summary=label[:120])
            if progress:
                progress(msg)

        assistant._orchestrator.progress = on_progress  # noqa: SLF001
        try:
            response = assistant.ask(
                request.question,
                database=database,
                execute=execute,
                resume_state=request.resume_state,
                user_reply=request.user_reply,
                memory=request.memory,
            )
        except Exception as exc:
            if type(exc).__name__ == "CancelledError" or "cancelled" in str(exc).lower():
                result.status = WorkflowStatus.CANCELLED
                result.completed_at = time.time()
                self._trace(result, "workflow_cancelled", "Workflow cancelled", "system")
                return result
            raise

        if getattr(response, "status", "completed") == "wait_user":
            result.status = WorkflowStatus.WAIT_USER
            result.answer_markdown = response.answer
            result.answer_plaintext = response.answer
            result.warnings = response.warnings or []
            result.pending_question = response.pending_question
            result.pending_options = list(response.pending_options or [])
            result.resume_state = response.resume_state
            result.next_actions = [
                NextAction(
                    label="Reply to continue",
                    action_type="reply",
                    payload={
                        "pending_question": response.pending_question,
                        "options": list(response.pending_options or []),
                    },
                ),
            ]
            self._trace(result, "waiting_for_user", "Waiting for user clarification", "agent")
            result.completed_at = time.time()
            return result

        result.status = WorkflowStatus.COMPLETED
        result.answer_markdown = response.answer
        result.answer_plaintext = response.answer
        result.warnings = response.warnings or []

        if response.sql:
            self._trace(result, "sql_generated", "SQL generated", "agent", output=response.sql)
            result.selected_sql = response.sql
            result.query_plan = self._build_query_plan(request.question, response.sql)
            result.sql_candidates.append(SQLCandidate(
                sql=response.sql,
                rationale="Generated by agent",
                confidence=0.8,
            ))
            self._trace(result, "sql_validation", "Validating SQL", "validation")
            result.validation_report = self._validate_sql(response.sql, database=database)

        if response.result:
            self._trace(
                result,
                "execution_completed",
                "Read-only query executed",
                "execution",
                summary=f"{response.result.row_count} rows in {response.result.elapsed_ms:.1f}ms",
            )
            result.execution_result = response.result

        self._trace(result, "result_interpreted", "Interpreting result", "agent")
        self._trace(result, "workflow_completed", "Workflow completed", "system")
        result.completed_at = time.time()
        return result

    def stream(self, request: WorkflowRequest) -> Iterator[TraceEvent | WorkflowResult]:
        """Execute a workflow with streaming trace events."""
        result = self.run(request)
        yield result

    def _build_assistant(self, request: WorkflowRequest) -> DataAssistant:
        adapter = self._get_adapter()
        session = self._get_session()
        return DataAssistant(
            adapter,
            session,
            self.llm,
            asset_store=self.asset_store,
            join_catalog=self.join_catalog,
            execution_policy=request.execution_policy,
        )

    def _get_adapter(self):
        if self._adapter is None:
            policy = None
            try:
                from dbaide.config import ConfigManager
                policy = ConfigManager().policy_for(self.connection)
            except Exception:
                policy = None
            self._adapter = build_adapter(self.connection, policy=policy, caller="agent")
        return self._adapter

    def _get_session(self):
        if self._session is None:
            try:
                from dbaide.config import ConfigManager
                policy = ConfigManager().policy_for(self.connection)
                self._session = Session.from_policy(self.connection, policy)
            except Exception:
                self._session = Session(self.connection)
        return self._session

    def _validate_sql(self, sql: str, *, database: str = "") -> ValidationReport:
        try:
            tools = QueryTools(self._get_adapter(), self._get_session().disclosure)
            _ = database
            return tools.validate_sql_report(sql, add_limit=True)
        except Exception as exc:
            return ValidationReport(
                ok=False,
                normalized_sql=sql,
                issues=[str(exc)],
                risk_level="rejected",
                requires_confirmation=True,
            )

    def _build_query_plan(self, question: str, sql: str) -> QueryPlan:
        tables = _extract_tables(sql)
        return QueryPlan(
            intent_summary=question,
            target_entities=tables,
            selected_columns=[],
            filters=[],
            joins=[],
            limit=100,
            assumptions=["Generated from available schema assets and guarded before execution."],
            confidence=0.8 if tables else 0.55,
        )

    def _trace(
        self,
        result: WorkflowResult,
        stage: str,
        title: str,
        actor: str,
        *,
        level: TraceLevel = TraceLevel.INFO,
        summary: str = "",
        output: str = "",
    ) -> None:
        event = TraceEvent(
            workflow_id=result.workflow_id,
            timestamp=time.time(),
            level=level,
            kind=_trace_kind(actor),
            stage=stage,
            actor=actor,
            title=title,
            summary=summary,
            output_preview=output[:500],
            status="completed",
        )
        result.trace.append(event)
        logger.debug("trace: %s - %s", stage, title)


class NullWorkflowEngine:
    """Null engine for when no connection is available."""

    def run(self, request: WorkflowRequest) -> WorkflowResult:
        return WorkflowResult(
            question=request.question,
            status=WorkflowStatus.FAILED,
            errors=[DBAideError(
                code=ErrorCode.CONNECTION_FAILED,
                stage="check_environment",
                message="No connection configured",
                hint="Add a connection first: dbaide connect add <name> --type <type>",
                repair_action=RepairAction.STOP,
            )],
            created_at=time.time(),
            completed_at=time.time(),
        )

    def stream(self, request: WorkflowRequest) -> Iterator[WorkflowResult]:
        yield self.run(request)


def _trace_kind(actor: str) -> TraceKind:
    if actor == "tool":
        return TraceKind.TOOL
    if actor == "validation":
        return TraceKind.VALIDATION
    if actor == "execution":
        return TraceKind.EXECUTION
    if actor == "agent":
        return TraceKind.AGENT
    return TraceKind.SYSTEM


def _extract_tables(sql: str) -> list[str]:
    tokens = sql.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]')
            if table and table.lower() not in {"select", "where"} and table not in tables:
                tables.append(table)
    return tables
