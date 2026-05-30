"""Structured error data structures for DBAide workflow engine."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Standardized error codes."""
    CONNECTION_FAILED = "CONNECTION_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    ASSET_MISSING = "ASSET_MISSING"
    ASSET_STALE = "ASSET_STALE"
    SCHEMA_LINK_LOW_CONFIDENCE = "SCHEMA_LINK_LOW_CONFIDENCE"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    UNSAFE_SQL = "UNSAFE_SQL"
    SQL_EXPLAIN_FAILED = "SQL_EXPLAIN_FAILED"
    SQL_EXECUTION_FAILED = "SQL_EXECUTION_FAILED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    EMPTY_RESULT = "EMPTY_RESULT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    USER_CANCELLED = "USER_CANCELLED"
    LLM_ERROR = "LLM_ERROR"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RISK_REJECTED = "RISK_REJECTED"


class RepairAction(str, Enum):
    """Suggested repair actions."""
    ASK_USER = "ask_user"
    CONFIRM = "confirm"
    REBUILD_ASSET = "rebuild_asset"
    REFRESH_SCHEMA = "refresh_schema"
    REPLAN = "replan"
    RERENDER_SQL = "rerender_sql"
    REVALIDATE = "revalidate"
    REEXECUTE = "reexecute"
    STOP = "stop"


class DBAideError:
    """Structured error with stage, hint, and repair action.

    All user-visible errors should use this instead of raw exceptions.
    """

    __slots__ = ("code", "stage", "message", "hint", "retryable", "repair_action", "evidence")

    def __init__(
        self,
        *,
        code: ErrorCode | str = ErrorCode.VALIDATION_FAILED,
        stage: str = "",
        message: str = "",
        hint: str = "",
        retryable: bool = False,
        repair_action: RepairAction | str = RepairAction.STOP,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.code = code if isinstance(code, ErrorCode) else ErrorCode(code)
        self.stage = stage
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.repair_action = repair_action if isinstance(repair_action, RepairAction) else RepairAction(repair_action)
        self.evidence = evidence or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "stage": self.stage,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "repair_action": self.repair_action.value,
            "evidence": self.evidence,
        }

    def __str__(self) -> str:
        parts = [f"[{self.code.value}]"]
        if self.stage:
            parts.append(f"at {self.stage}:")
        parts.append(self.message)
        if self.hint:
            parts.append(f"Hint: {self.hint}")
        return " ".join(parts)

    @classmethod
    def from_exception(cls, exc: Exception, *, stage: str = "", code: ErrorCode = ErrorCode.VALIDATION_FAILED) -> DBAideError:
        """Create a DBAideError from a raw exception."""
        return cls(
            code=code,
            stage=stage,
            message=str(exc),
            hint="",
            retryable=False,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": type(exc).__name__},
        )
