"""Workflow engine that wraps existing DataAssistant with trace and structured results."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from dbaide.adapters import build_adapter
from dbaide.agent.progress_events import progress_event, progress_label
from dbaide.agent import DataAssistant
from dbaide.core.cancellation import CancelledError
from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.result import (
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
        model_config: "Any | None" = None,
    ) -> None:
        self.connection = connection
        self.llm = llm
        self.asset_store = asset_store or AssetStore()
        self.join_catalog = join_catalog or JoinCatalogStore()
        self._model_config = model_config
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
            created_at=time.time(),
        )

        result.status = WorkflowStatus.RUNNING
        self._trace(result, "workflow_started", "Workflow started", "agent", summary=request.question)
        self._live_progress(
            progress,
            stage="workflow_started",
            title=_workflow_progress_title("trace.starting"),
            status="running",
            kind="agent",
        )

        env_title = _workflow_progress_title("trace.phase.environment_check")
        self._trace(result, "environment_check", env_title, "system", status="running")
        self._live_progress(
            progress,
            stage="environment_check",
            title=env_title,
            status="running",
            kind="phase",
            node_id="workflow:environment_check",
        )
        try:
            adapter = self._get_adapter()
            adapter.test()
        except Exception as exc:
            self._live_progress(
                progress,
                stage="environment_check",
                title=env_title,
                status="failed",
                kind="phase",
                node_id="workflow:environment_check",
                detail=str(exc)[:240],
            )
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
            self._trace(
                result, "workflow_failed", "Workflow failed", "system",
                level=TraceLevel.ERROR, summary=str(exc), status="failed",
            )
            return result
        self._trace(result, "environment_check", env_title, "system")
        self._live_progress(
            progress,
            stage="environment_check",
            title=env_title,
            status="completed",
            kind="phase",
            node_id="workflow:environment_check",
        )

        database = request.database_scope[0] if request.database_scope else ""
        execute = True
        self._trace(result, "planning", "Planning guarded read-only workflow", "agent")
        self._live_progress(
            progress,
            stage="planning",
            title=_workflow_progress_title("trace.phase.agent_request"),
            status="running",
            kind="agent",
        )

        assistant = self._build_assistant(request)
        self._trace(result, "agent_request", "Running assistant", "agent")

        def on_progress(msg: str | dict[str, Any]) -> None:
            if cancel_check:
                cancel_check()
            # Streamed answer slices are UI-only — never persisted to the trace.
            if isinstance(msg, dict) and msg.get("kind") == "answer_chunk":
                if progress:
                    try:
                        progress(msg)
                    except Exception:
                        pass
                return
            label = progress_label(msg)
            if isinstance(msg, dict):
                # Stash the full progress event in metadata so the persisted trace
                # keeps every detail (args, options, clarification questions, sql, …)
                # for a complete copy/export — the summary alone is lossy.
                self._trace(
                    result,
                    str(msg.get("stage") or "agent_progress"),
                    label,
                    str(msg.get("kind") or "agent"),
                    summary=str(msg.get("detail") or label)[:120],
                    status=str(msg.get("status") or "completed"),
                    duration_ms=float(msg.get("duration_ms") or 0),
                    metadata=dict(msg),
                )
            else:
                self._trace(result, "agent_progress", msg, "agent", summary=label[:120])
            if progress:
                try:
                    progress(msg)
                except Exception:
                    pass

        assistant._orchestrator.progress = on_progress  # noqa: SLF001
        assistant._orchestrator.cancel_check = cancel_check  # noqa: SLF001
        # User-pinned schema scope (composer attachments) → discovery prioritises it.
        assistant._orchestrator.schema_scope = request.schema_scope or {}  # noqa: SLF001
        # Stream the final answer token-by-token when the user enabled it.
        assistant._orchestrator.stream_answers = bool(request.stream_answers)  # noqa: SLF001
        # Session memory (prior completed turns in this chat session + the
        # consolidated user-confirmed criteria from those turns).
        assistant._orchestrator.session_turns = list(request.session_turns or [])  # noqa: SLF001
        assistant._orchestrator.active_criteria = list(request.active_criteria or [])  # noqa: SLF001
        # Session-level message continuity: pass the persisted message stream
        # so the agent appends to the existing conversation instead of starting fresh.
        if request.session_messages is not None:
            from dbaide.llm import LLMMessage
            assistant._orchestrator.session_messages = [  # noqa: SLF001
                LLMMessage(m["role"], m["content"])
                for m in request.session_messages
                if isinstance(m, dict) and "role" in m and "content" in m
            ]
        try:
            response = assistant.ask(
                request.question,
                database=database,
                execute=execute,
                resume_state=request.resume_state,
                user_reply=request.user_reply,
            )
        except CancelledError:
            result.status = WorkflowStatus.CANCELLED
            result.completed_at = time.time()
            self._trace(result, "workflow_cancelled", "Workflow cancelled", "system")
            return result

        if getattr(response, "status", "completed") == "wait_user":
            updated_msgs = getattr(assistant._orchestrator, "session_messages", None)  # noqa: SLF001
            if updated_msgs is not None:
                result.session_messages = [
                    {"role": m.role, "content": m.content} for m in updated_msgs
                ]
            result.status = WorkflowStatus.WAIT_USER
            result.answer_markdown = response.answer
            result.answer_plaintext = response.answer
            result.warnings = response.warnings or []
            result.pending_question = response.pending_question
            result.pending_options = list(response.pending_options or [])
            result.pending_questions = list(getattr(response, "pending_questions", []) or [])
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

        # Capture the updated session messages for persistence.
        updated_msgs = getattr(assistant._orchestrator, "session_messages", None)  # noqa: SLF001
        if updated_msgs is not None:
            result.session_messages = [
                {"role": m.role, "content": m.content} for m in updated_msgs
            ]
        fail_reason = str(getattr(assistant._orchestrator.run_state, "fail_reason", "") or "")  # noqa: SLF001
        result.status = WorkflowStatus.FAILED if fail_reason else WorkflowStatus.COMPLETED
        result.answer_markdown = response.answer
        result.answer_plaintext = response.answer
        result.warnings = response.warnings or []
        # Snapshot session-memory-relevant state from the orchestrator: criteria
        # the user confirmed in THIS turn (so future turns inherit them) and the
        # tables this turn touched (so the next follow-up can see the context).
        run_state = assistant._orchestrator.run_state  # noqa: SLF001
        result.clarifications = list(getattr(run_state, "clarifications", []) or [])
        result.disclosed_tables = sorted({str(k) for k in (run_state.schemas or {}).keys()})
        result.charts = list(getattr(response, "charts", None) or getattr(run_state, "charts", []) or [])
        executed_sqls = list(getattr(response, "executed_sqls", None) or [])
        result.executed_sqls = executed_sqls

        selected = str(response.sql or "").strip()
        if not selected and executed_sqls:
            selected = str(executed_sqls[-1].get("sql") or "").strip()
        if selected:
            self._trace(result, "sql_generated", "SQL generated", "agent", output=selected)
            result.selected_sql = selected
            result.query_plan = self._build_query_plan(request.question, selected, limit=request.limit)
            result.sql_candidates.append(SQLCandidate(
                sql=selected,
                rationale="Generated by agent",
                confidence=0.8,
            ))
            self._trace(result, "sql_validation", "Validating SQL", "validation")
            result.validation_report = self._validate_sql(selected, database=database, limit=request.limit)

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
        if fail_reason:
            self._trace(
                result,
                "workflow_failed",
                "Workflow failed",
                "system",
                level=TraceLevel.ERROR,
                summary=fail_reason,
            )
        else:
            self._trace(result, "workflow_completed", "Workflow completed", "system")
        result.completed_at = time.time()
        return result

    def _build_assistant(self, request: WorkflowRequest) -> DataAssistant:
        from dataclasses import fields as dc_fields
        adapter = self._get_adapter()
        base_session = self._get_session()
        session = Session(**{f.name: getattr(base_session, f.name) for f in dc_fields(base_session)})
        if request.limit:
            session.default_limit = max(1, int(request.limit))
        if request.timeout_seconds:
            session.timeout_seconds = max(1, int(request.timeout_seconds))
        return DataAssistant(
            adapter,
            session,
            self.llm,
            asset_store=self.asset_store,
            join_catalog=self.join_catalog,
            model_config=self._model_config,
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

    def _validate_sql(self, sql: str, *, database: str = "", limit: int | None = None) -> ValidationReport:
        try:
            tools = QueryTools(self._get_adapter(), self._get_session().disclosure)
            _ = database
            return tools.validate_sql_report(sql, add_limit=True, limit=limit)
        except Exception as exc:
            return ValidationReport(
                ok=False,
                normalized_sql=sql,
                issues=[str(exc)],
                risk_level="rejected",
                requires_confirmation=True,
            )

    def _build_query_plan(self, question: str, sql: str, *, limit: int = 100) -> QueryPlan:
        tables = _extract_tables(sql)
        return QueryPlan(
            intent_summary=question,
            target_entities=tables,
            selected_columns=[],
            filters=[],
            joins=[],
            limit=max(1, int(limit or 100)),
            assumptions=[],
            confidence=0.0,
        )

    def _live_progress(
        self,
        progress: Callable[..., None] | None,
        *,
        stage: str,
        title: str,
        status: str = "running",
        kind: str = "phase",
        node_id: str = "",
        detail: str = "",
    ) -> None:
        """Stream a prelude/progress dict to the GUI (environment check, planning, …)."""
        if not progress:
            return
        payload = progress_event(
            stage=stage,
            title=title,
            status=status,
            kind=kind,
            node_id=node_id or f"workflow:{stage}",
        )
        if detail:
            payload["detail"] = detail
        progress(payload)

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
        status: str = "completed",
        duration_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
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
            duration_ms=duration_ms,
            status=status,
            metadata=metadata or {},
        )
        result.trace.append(event)
        logger.debug("trace: %s - %s", stage, title)


def _workflow_progress_title(key: str) -> str:
    try:
        from dbaide.i18n import t
        return t(key)
    except Exception:
        return key


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
    from dbaide.validation.sql_cleanup import strip_function_from_keywords

    # Strip FROM inside SQL functions (EXTRACT, TRIM, SUBSTRING) so that
    # column names are not mistaken for table references.
    cleaned = strip_function_from_keywords(sql)
    tokens = cleaned.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]')
            if table and table.lower() not in {"select", "where"} and table not in tables:
                tables.append(table)
    return tables
