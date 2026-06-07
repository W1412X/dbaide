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
