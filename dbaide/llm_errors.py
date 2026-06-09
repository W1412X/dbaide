"""Map raw LLM / model exceptions to structured errors and user-facing text."""
from __future__ import annotations

import re
from typing import Any

from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.i18n import t as _t

_AUTH_MARKERS = ("401", "403", "unauthorized", "invalid api key", "authentication", "invalid_api_key")
_RATE_MARKERS = ("429", "rate limit", "too many requests", "quota")
_TIMEOUT_MARKERS = ("timeout", "timed out", "deadline")
_NETWORK_MARKERS = ("connection refused", "connection reset", "network", "unreachable", "dns", "ssl", "urlopen error")


def classify_llm_error(exc: BaseException, *, stage: str = "llm") -> DBAideError:
    """Turn a model/LLM failure into a structured ``DBAideError``."""
    message = str(exc or "").strip()
    lower = message.lower()
    exc_type = type(exc).__name__

    if "no llm model configured" in lower or exc_type == "NullLLMClient":
        return DBAideError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            stage=stage,
            message=message or "No LLM configured",
            hint=_t("error.llm.unconfigured"),
            retryable=False,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    if any(marker in lower for marker in _AUTH_MARKERS):
        return DBAideError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            stage=stage,
            message=message,
            hint=_t("error.llm.auth"),
            retryable=False,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    if any(marker in lower for marker in _RATE_MARKERS):
        return DBAideError(
            code=ErrorCode.LLM_ERROR,
            stage=stage,
            message=message,
            hint=_t("error.llm.rate_limit"),
            retryable=True,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    if any(marker in lower for marker in _TIMEOUT_MARKERS):
        return DBAideError(
            code=ErrorCode.LLM_ERROR,
            stage=stage,
            message=message,
            hint=_t("error.llm.timeout"),
            retryable=True,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    if any(marker in lower for marker in _NETWORK_MARKERS):
        return DBAideError(
            code=ErrorCode.LLM_ERROR,
            stage=stage,
            message=message,
            hint=_t("error.llm.network"),
            retryable=True,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    if re.search(r"\b5\d{2}\b", message):
        return DBAideError(
            code=ErrorCode.LLM_ERROR,
            stage=stage,
            message=message,
            hint=_t("error.llm.server"),
            retryable=True,
            repair_action=RepairAction.STOP,
            evidence={"exception_type": exc_type},
        )

    return DBAideError(
        code=ErrorCode.LLM_ERROR,
        stage=stage,
        message=message or exc_type,
        hint=_t("error.llm.generic"),
        retryable=False,
        repair_action=RepairAction.STOP,
        evidence={"exception_type": exc_type},
    )


def is_llm_related(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    if "llm" in message or "model" in message or "openai" in message:
        return True
    if any(marker in message for marker in (*_AUTH_MARKERS, *_RATE_MARKERS, "no llm model configured")):
        return True
    return type(exc).__name__ in ("LoopDecisionError", "RuntimeError", "ValueError") and (
        "complete" in message or "decision" in message
    )


def user_message_for_error(err: DBAideError | dict[str, Any]) -> str:
    """Short string suitable for toasts / turn errors."""
    if isinstance(err, DBAideError):
        hint = str(err.hint or "").strip()
        msg = str(err.message or "").strip()
        return hint or msg or str(err)
    hint = str((err or {}).get("hint") or "").strip()
    msg = str((err or {}).get("message") or "").strip()
    return hint or msg or _t("error.llm.generic")


# ── Common exception markers for non-LLM errors ──────────────────────────

_CONN_MARKERS = (
    "connection refused", "connection reset", "can't connect",
    "could not connect", "unable to connect", "no such host",
    "unknown host", "not found in pg_hba", "access denied",
    "operationalerror", "connection timed out",
)
_PERMISSION_MARKERS = (
    "permission denied", "access denied for user", "insufficient privilege",
    "not authorized", "forbidden",
)
_TIMEOUT_DB_MARKERS = (
    "query_canceled", "statement timeout", "canceling statement",
    "lock wait timeout", "execution expired",
)
_SYNTAX_MARKERS = (
    "syntax error", "you have an error in your sql",
    "near \"", "unexpected token",
)
_NO_TABLE_MARKERS = (
    "no such table", "table or view not found",
    "relation \"", "doesn't exist",
    "unknown table", "table not found",
)
_NO_COLUMN_MARKERS = (
    "no such column", "unknown column", "column not found",
    "column \"", "does not exist",
)


def format_user_error(exc: object) -> str:
    """Classify *any* exception into a user-friendly, i18n-aware message.

    Covers LLM errors (via ``classify_llm_error``), database errors, and
    generic Python exceptions.  Falls back to ``error.generic`` for truly
    unknown failures.
    """
    if isinstance(exc, BaseException) and is_llm_related(exc):
        return user_message_for_error(classify_llm_error(exc))

    message = str(exc or "").strip()
    lower = message.lower()

    # Cancelled — caller should normally handle this already.
    if isinstance(exc, BaseException) and type(exc).__name__ in ("CancelledError", "KeyboardInterrupt"):
        return _t("toast.cancelled")

    # Permission / privilege (must be checked before OSError — PermissionError
    # is a subclass of OSError).
    if isinstance(exc, PermissionError):
        return _t("error.permission")
    if any(m in lower for m in _PERMISSION_MARKERS):
        return _t("error.permission")

    # Timeouts (DB-side or Python-side; checked before OSError — TimeoutError
    # is a subclass of OSError).
    if isinstance(exc, TimeoutError):
        return _t("error.timeout")
    if any(m in lower for m in _TIMEOUT_MARKERS):
        return _t("error.timeout")

    # Database connection problems
    if isinstance(exc, (ConnectionError, OSError)):
        return _t("error.connection")
    if any(m in lower for m in _CONN_MARKERS):
        return _t("error.connection")

    # SQL syntax
    if any(m in lower for m in _SYNTAX_MARKERS):
        return _t("error.sql_syntax")

    # Missing table / view
    if any(m in lower for m in _NO_TABLE_MARKERS):
        return _t("error.table_not_found")

    # Missing column
    if any(m in lower for m in _NO_COLUMN_MARKERS):
        return _t("error.column_not_found")

    # File not found (e.g. SQLite path)
    if isinstance(exc, FileNotFoundError):
        return _t("error.connection")

    # Fall back: use the raw message inside a generic wrapper when it's
    # short enough to be informative; otherwise the generic catch-all.
    if message and len(message) < 200:
        return _t("error.sql_execution", detail=message)
    return _t("error.generic")
