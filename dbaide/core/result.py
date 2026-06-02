"""Core result data structures for DBAide workflow engine."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    """Workflow execution status."""
    PENDING = "pending"
    RUNNING = "running"
    WAIT_USER = "wait_user"
    NEED_CONFIRM = "need_confirm"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionPolicy(str, Enum):
    """SQL execution policy."""
    INSPECT_ONLY = "inspect_only"
    SQL_ONLY = "sql_only"
    SAFE_AUTO = "safe_auto"
    EXPERT = "expert"


class WorkflowResult:
    """Unified workflow result consumed by CLI and GUI.

    Schema version: 1
    """

    __slots__ = (
        "schema_version", "workflow_id", "status", "question",
        "connection_name", "database_scope", "execution_policy",
        "answer_markdown", "answer_plaintext",
        "query_plan", "sql_candidates", "selected_sql",
        "validation_report", "execution_result",
        "assumptions", "warnings", "errors", "next_actions",
        "trace", "created_at", "completed_at",
        "pending_question", "pending_options", "resume_state",
    )

    def __init__(
        self,
        *,
        workflow_id: str = "",
        status: WorkflowStatus = WorkflowStatus.PENDING,
        question: str = "",
        connection_name: str = "",
        database_scope: list[str] | None = None,
        execution_policy: ExecutionPolicy = ExecutionPolicy.SAFE_AUTO,
        answer_markdown: str = "",
        answer_plaintext: str = "",
        query_plan: QueryPlan | None = None,
        sql_candidates: list[SQLCandidate] | None = None,
        selected_sql: str = "",
        validation_report: ValidationReport | None = None,
        execution_result: Any = None,
        assumptions: list[str] | None = None,
        warnings: list[str] | None = None,
        errors: list[DBAideError] | None = None,
        next_actions: list[NextAction] | None = None,
        trace: list[TraceEvent] | None = None,
        created_at: float = 0.0,
        completed_at: float = 0.0,
        pending_question: str = "",
        pending_options: list[str] | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        self.schema_version = 1
        self.workflow_id = workflow_id or str(uuid.uuid4())[:8]
        self.status = status
        self.question = question
        self.connection_name = connection_name
        self.database_scope = database_scope or []
        self.execution_policy = execution_policy
        self.answer_markdown = answer_markdown
        self.answer_plaintext = answer_plaintext
        self.query_plan = query_plan
        self.sql_candidates = sql_candidates or []
        self.selected_sql = selected_sql
        self.validation_report = validation_report
        self.execution_result = execution_result
        self.assumptions = assumptions or []
        self.warnings = warnings or []
        self.errors = errors or []
        self.next_actions = next_actions or []
        self.trace = trace or []
        self.created_at = created_at
        self.completed_at = completed_at
        self.pending_question = pending_question
        self.pending_options = pending_options or []
        self.resume_state = resume_state

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "question": self.question,
            "connection_name": self.connection_name,
            "database_scope": self.database_scope,
            "execution_policy": self.execution_policy.value,
            "answer_markdown": self.answer_markdown,
            "answer_plaintext": self.answer_plaintext,
            "query_plan": self.query_plan.to_dict() if self.query_plan else None,
            "sql_candidates": [c.to_dict() for c in self.sql_candidates],
            "selected_sql": self.selected_sql,
            "validation_report": self.validation_report.to_dict() if self.validation_report else None,
            "execution_result": _serialize_execution_result(self.execution_result),
            "assumptions": self.assumptions,
            "warnings": self.warnings,
            "errors": [e.to_dict() for e in self.errors],
            "next_actions": [a.to_dict() for a in self.next_actions],
            "trace": [e.to_dict() for e in self.trace],
            "trace_summary": f"{len(self.trace)} events",
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "pending_question": self.pending_question,
            "pending_options": self.pending_options,
            "resume_state": self.resume_state,
        }


class WorkflowRequest:
    """Request to start a workflow."""

    __slots__ = (
        "question", "connection_name", "database_scope", "mode",
        "execution_policy", "limit", "timeout_seconds", "model_name",
        "show_trace", "resume_state", "user_reply", "memory",
    )

    def __init__(
        self,
        *,
        question: str = "",
        connection_name: str = "",
        database_scope: list[str] | None = None,
        mode: str = "ask",
        execution_policy: ExecutionPolicy = ExecutionPolicy.SAFE_AUTO,
        limit: int = 100,
        timeout_seconds: int = 10,
        model_name: str = "",
        show_trace: bool = False,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
        memory: str = "",
    ) -> None:
        self.question = question
        self.connection_name = connection_name
        self.database_scope = database_scope or []
        self.mode = mode
        self.execution_policy = execution_policy
        self.limit = limit
        self.timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.show_trace = show_trace
        self.resume_state = resume_state
        self.user_reply = user_reply
        # Rendered "known answers to similar past questions" block (memory mechanism).
        self.memory = memory


class QueryPlan:
    """Structured query plan (business semantics, not SQL)."""

    __slots__ = (
        "intent_summary", "target_entities", "selected_columns",
        "filters", "joins", "aggregations", "group_by", "order_by",
        "limit", "time_range", "output_columns", "assumptions",
        "confidence", "missing_information",
    )

    def __init__(
        self,
        *,
        intent_summary: str = "",
        target_entities: list[str] | None = None,
        selected_columns: list[str] | None = None,
        filters: list[str] | None = None,
        joins: list[dict[str, Any]] | None = None,
        aggregations: list[str] | None = None,
        group_by: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
        time_range: str = "",
        output_columns: list[str] | None = None,
        assumptions: list[str] | None = None,
        confidence: float = 0.0,
        missing_information: list[str] | None = None,
    ) -> None:
        self.intent_summary = intent_summary
        self.target_entities = target_entities or []
        self.selected_columns = selected_columns or []
        self.filters = filters or []
        self.joins = joins or []
        self.aggregations = aggregations or []
        self.group_by = group_by or []
        self.order_by = order_by or []
        self.limit = limit
        self.time_range = time_range
        self.output_columns = output_columns or []
        self.assumptions = assumptions or []
        self.confidence = confidence
        self.missing_information = missing_information or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_summary": self.intent_summary,
            "target_entities": self.target_entities,
            "selected_columns": self.selected_columns,
            "filters": self.filters,
            "joins": self.joins,
            "aggregations": self.aggregations,
            "group_by": self.group_by,
            "order_by": self.order_by,
            "limit": self.limit,
            "time_range": self.time_range,
            "output_columns": self.output_columns,
            "assumptions": self.assumptions,
            "confidence": self.confidence,
            "missing_information": self.missing_information,
        }


class SQLCandidate:
    """A candidate SQL with metadata."""

    __slots__ = ("sql", "rationale", "confidence", "expected_columns", "dialect")

    def __init__(
        self,
        *,
        sql: str = "",
        rationale: str = "",
        confidence: float = 0.0,
        expected_columns: list[str] | None = None,
        dialect: str = "",
    ) -> None:
        self.sql = sql
        self.rationale = rationale
        self.confidence = confidence
        self.expected_columns = expected_columns or []
        self.dialect = dialect

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "expected_columns": self.expected_columns,
            "dialect": self.dialect,
        }


class ValidationReport:
    """SQL validation report."""

    __slots__ = (
        "ok", "normalized_sql", "issues", "warnings",
        "risk_level", "requires_confirmation", "explain_summary",
    )

    def __init__(
        self,
        *,
        ok: bool = True,
        normalized_sql: str = "",
        issues: list[str] | None = None,
        warnings: list[str] | None = None,
        risk_level: str = "low",
        requires_confirmation: bool = False,
        explain_summary: str = "",
    ) -> None:
        self.ok = ok
        self.normalized_sql = normalized_sql
        self.issues = issues or []
        self.warnings = warnings or []
        self.risk_level = risk_level
        self.requires_confirmation = requires_confirmation
        self.explain_summary = explain_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "normalized_sql": self.normalized_sql,
            "issues": self.issues,
            "warnings": self.warnings,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "explain_summary": self.explain_summary,
        }


class NextAction:
    """Suggested next action for the user."""

    __slots__ = ("label", "action_type", "payload")

    def __init__(self, label: str, action_type: str = "", payload: dict[str, Any] | None = None) -> None:
        self.label = label
        self.action_type = action_type
        self.payload = payload or {}

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "action_type": self.action_type, "payload": self.payload}


class AnswerCard:
    """Structured answer card for GUI rendering."""

    __slots__ = (
        "title", "summary_markdown", "status_badges", "sql_block",
        "result_table", "assumptions", "warnings", "actions",
        "source_workflow_id",
    )

    def __init__(self, **kwargs) -> None:
        self.title = kwargs.get("title", "")
        self.summary_markdown = kwargs.get("summary_markdown", "")
        self.status_badges = kwargs.get("status_badges", [])
        self.sql_block = kwargs.get("sql_block", "")
        self.result_table = kwargs.get("result_table")
        self.assumptions = kwargs.get("assumptions", [])
        self.warnings = kwargs.get("warnings", [])
        self.actions = kwargs.get("actions", [])
        self.source_workflow_id = kwargs.get("source_workflow_id", "")


class ResultTable:
    """Structured result table for GUI rendering."""

    __slots__ = (
        "columns", "rows", "row_count", "displayed_row_count",
        "truncated", "limit", "elapsed_ms", "column_metadata",
    )

    def __init__(self, **kwargs) -> None:
        self.columns = kwargs.get("columns", [])
        self.rows = kwargs.get("rows", [])
        self.row_count = kwargs.get("row_count", 0)
        self.displayed_row_count = kwargs.get("displayed_row_count", 0)
        self.truncated = kwargs.get("truncated", False)
        self.limit = kwargs.get("limit", 100)
        self.elapsed_ms = kwargs.get("elapsed_ms", 0.0)
        self.column_metadata = kwargs.get("column_metadata", [])


# Import TraceEvent and DBAideError from their modules
from dbaide.core.events import TraceEvent  # noqa: E402
from dbaide.core.errors import DBAideError  # noqa: E402


def _serialize_execution_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    return {
        "columns": getattr(result, "columns", []),
        "rows": getattr(result, "rows", []),
        "row_count": getattr(result, "row_count", 0),
        "truncated": bool(getattr(result, "truncated", False)),
        "sql": getattr(result, "sql", ""),
        "elapsed_ms": float(getattr(result, "elapsed_ms", 0) or 0),
    }
