"""Core data structures for DBAide workflow engine."""
from dbaide.core.result import (
    WorkflowStatus,
    WorkflowResult,
    WorkflowRequest,
    QueryPlan,
    SQLCandidate,
    ValidationReport,
    NextAction,
    AnswerCard,
    ResultTable,
)
from dbaide.core.events import TraceEvent, TraceLevel, TraceKind
from dbaide.core.errors import DBAideError, ErrorCode, RepairAction

__all__ = [
    "WorkflowStatus",
    "WorkflowResult",
    "WorkflowRequest",
    "QueryPlan",
    "SQLCandidate",
    "ValidationReport",
    "NextAction",
    "AnswerCard",
    "ResultTable",
    "TraceEvent",
    "TraceLevel",
    "TraceKind",
    "DBAideError",
    "ErrorCode",
    "RepairAction",
]
