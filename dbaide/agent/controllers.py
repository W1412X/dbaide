"""Risk controller, result interpreter, error router for DBAide."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.core.result import ValidationReport

logger = logging.getLogger("dbaide.agent.controllers")


# ─────────────────────────────────────────────────────────────────────────────
# RiskController
# ─────────────────────────────────────────────────────────────────────────────

class RiskDecision:
    """Decision from risk controller."""

    __slots__ = ("action", "reason", "risk_level", "requires_confirmation")

    def __init__(self, action: str, reason: str, risk_level: str = "low",
                 requires_confirmation: bool = False) -> None:
        self.action = action
        self.reason = reason
        self.risk_level = risk_level
        self.requires_confirmation = requires_confirmation

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
        }


class RiskController:
    """Controls whether SQL can be auto-executed.

    Inspired by Codex's approval workflow.
    """

    def decide(
        self,
        *,
        validation: ValidationReport,
        plan_confidence: float = 0.0,
        table_count: int = 1,
        has_joins: bool = False,
        join_confidence: float = 1.0,
        estimated_rows: int | None = None,
        explain_max_rows: int = 0,
    ) -> RiskDecision:
        """Decide whether to execute, confirm, or reject."""

        # Validation failed - reject
        if not validation.ok:
            return RiskDecision("reject", "SQL validation failed", "rejected")

        # EXPLAIN cost gate: estimated scan far too large.
        if explain_max_rows > 0 and estimated_rows is not None and estimated_rows > explain_max_rows:
            return RiskDecision(
                "confirm",
                f"EXPLAIN estimates ~{estimated_rows:,} rows (limit {explain_max_rows:,})",
                "high",
                requires_confirmation=True,
            )

        # High risk from validation
        if validation.risk_level == "high":
            return RiskDecision(
                "confirm",
                f"High risk: {'; '.join(validation.warnings[:3])}",
                "high",
                requires_confirmation=True,
            )

        # Low confidence plan
        if plan_confidence < 0.65:
            return RiskDecision(
                "confirm",
                f"Low plan confidence ({plan_confidence:.2f})",
                "medium",
                requires_confirmation=True,
            )

        # Low confidence joins
        if has_joins and join_confidence < 0.8:
            return RiskDecision(
                "confirm",
                f"Low join confidence ({join_confidence:.2f})",
                "medium",
                requires_confirmation=True,
            )

        # Default: safe auto
        if validation.requires_confirmation:
            return RiskDecision(
                "confirm",
                "Validation requires confirmation",
                validation.risk_level,
                requires_confirmation=True,
            )

        return RiskDecision("auto_execute", "Low risk, safe to execute", "low")


# ─────────────────────────────────────────────────────────────────────────────
# ResultInterpreter
# ─────────────────────────────────────────────────────────────────────────────

class ResultInterpreter:
    """Interprets query results and generates natural language explanations.

    Only explains actual results - never fabricates.
    """

    def interpret(
        self,
        *,
        question: str,
        sql: str,
        row_count: int,
        columns: list[str],
        elapsed_ms: float,
        truncated: bool,
        warnings: list[str],
        language: str | None = None,
    ) -> dict[str, Any]:
        """Interpret query result."""
        zh = _prefers_chinese(question, language)
        parts = []

        if row_count == 0:
            parts.append("查询未返回任何行。" if zh else "The query returned no rows.")
        elif row_count == 1:
            parts.append("查询返回 1 条记录。" if zh else "The query returned 1 row.")
        else:
            parts.append(
                f"查询共返回 {row_count:,} 条记录。"
                if zh
                else f"The query returned {row_count:,} rows."
            )

        if truncated:
            # row_count is the number of rows RETURNED (the cap), not the true total —
            # more rows exist. Don't call it the total (the old wording did, misleadingly).
            parts.append(
                f"注意：结果已截断，仅展示前 {row_count:,} 条，实际行数更多。"
                if zh
                else f"Note: results were truncated — showing the first {row_count:,} rows; more rows exist."
            )

        if elapsed_ms > 5000:
            parts.append(
                f"查询耗时 {elapsed_ms / 1000:.1f}s。"
                if zh
                else f"Query took {elapsed_ms/1000:.1f}s."
            )

        if warnings:
            label = "提示：" if zh else "Warnings:"
            parts.append(label)
            for w in warnings:
                parts.append(f"  - {w}")

        return {
            "summary": "\n".join(parts),
            "assumptions": [],
            "next_actions": [],
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
        }


def _prefers_chinese(text: str, language: str | None = None) -> bool:
    from dbaide.i18n import detect_user_language, normalize
    return normalize(language) == "zh" if language else detect_user_language(text) == "zh"
