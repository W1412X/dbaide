"""Risk controller, result interpreter, error router for DBAide."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.core.result import ExecutionPolicy, ValidationReport

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
        policy: ExecutionPolicy,
        validation: ValidationReport,
        plan_confidence: float = 0.0,
        table_count: int = 1,
        has_joins: bool = False,
        join_confidence: float = 1.0,
    ) -> RiskDecision:
        """Decide whether to execute, confirm, or reject."""

        # Policy: inspect only - never execute
        if policy == ExecutionPolicy.INSPECT_ONLY:
            return RiskDecision("reject", "Inspect-only policy", "low")

        # Policy: SQL only - generate but don't execute
        if policy == ExecutionPolicy.SQL_ONLY:
            return RiskDecision("generate_only", "SQL-only policy", "low")

        # Validation failed - reject
        if not validation.ok:
            return RiskDecision("reject", "SQL validation failed", "rejected")

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

        # Complex query
        if table_count > 2:
            return RiskDecision(
                "confirm",
                f"Query involves {table_count} tables",
                "medium",
                requires_confirmation=True,
            )

        # Policy: expert - allow more
        if policy == ExecutionPolicy.EXPERT:
            return RiskDecision("auto_execute", "Expert policy, low risk", "low")

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
    ) -> dict[str, Any]:
        """Interpret query result."""
        zh = _prefers_chinese(question)
        parts = []

        if row_count == 0:
            if zh:
                parts.append("查询未返回任何行，可能原因：")
                parts.append("- 筛选条件过严或字段值不匹配")
                parts.append("- 目标表当前没有符合条件的数据")
                parts.append("- 关联的表或时间范围不正确")
            else:
                parts.append("The query returned no rows. This could mean:")
                parts.append("- The filter conditions are too restrictive")
                parts.append("- The data doesn't match the expected criteria")
                parts.append("- The table is empty")
        elif row_count == 1:
            parts.append("查询返回 1 条记录。" if zh else "The query returned 1 row.")
        else:
            parts.append(
                f"查询共返回 {row_count:,} 条记录。"
                if zh
                else f"The query returned {row_count:,} rows."
            )

        if truncated:
            parts.append(
                f"注意：结果已截断，界面仅展示部分数据（总计 {row_count:,} 条）。"
                if zh
                else f"Note: Results were truncated. Only showing a subset of {row_count:,} total rows."
            )

        if elapsed_ms > 5000:
            parts.append(
                f"查询耗时 {elapsed_ms / 1000:.1f}s，可考虑加索引或缩小范围。"
                if zh
                else f"Query took {elapsed_ms/1000:.1f}s - consider adding indexes or reducing scope."
            )

        if warnings:
            label = "提示：" if zh else "Warnings:"
            parts.append(label)
            for w in warnings:
                parts.append(f"  - {w}")

        assumptions = []
        if "date" in sql.lower() or "time" in sql.lower():
            assumptions.append("查询包含时间筛选" if zh else "Query involves date/time filtering")
        if "join" in sql.lower():
            assumptions.append("查询涉及多表关联" if zh else "Query joins multiple tables")

        next_actions = []
        if row_count == 0:
            next_actions.append("放宽 WHERE 条件或检查样例数据" if zh else "Try relaxing filter conditions")
            next_actions.append("确认表名、字段名和时间范围" if zh else "Check if the table has data")
        elif truncated:
            next_actions.append("增加更具体的筛选以减少结果集" if zh else "Add more specific filters to reduce result set")
        if elapsed_ms > 10000:
            next_actions.append("运行 EXPLAIN 查看执行计划" if zh else "Consider running EXPLAIN to check query plan")

        return {
            "summary": "\n".join(parts),
            "assumptions": assumptions,
            "next_actions": next_actions,
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
        }


def _prefers_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ─────────────────────────────────────────────────────────────────────────────
# ErrorRouter
# ─────────────────────────────────────────────────────────────────────────────

class ErrorRouter:
    """Routes errors to repair actions.

    Inspired by Claude Code's self-correction pattern.
    """

    # Error code -> repair action mapping
    REPAIR_MAP = {
        ErrorCode.UNKNOWN_TABLE: RepairAction.REFRESH_SCHEMA,
        ErrorCode.UNKNOWN_COLUMN: RepairAction.REFRESH_SCHEMA,
        ErrorCode.UNSAFE_SQL: RepairAction.STOP,
        ErrorCode.SQL_EXPLAIN_FAILED: RepairAction.RERENDER_SQL,
        ErrorCode.SQL_EXECUTION_FAILED: RepairAction.RERENDER_SQL,
        ErrorCode.QUERY_TIMEOUT: RepairAction.REPLAN,
        ErrorCode.EMPTY_RESULT: RepairAction.ASK_USER,
        ErrorCode.ASSET_MISSING: RepairAction.REBUILD_ASSET,
        ErrorCode.ASSET_STALE: RepairAction.REBUILD_ASSET,
        ErrorCode.MODEL_UNAVAILABLE: RepairAction.STOP,
        ErrorCode.LLM_ERROR: RepairAction.REPLAN,
        ErrorCode.CONNECTION_FAILED: RepairAction.STOP,
        ErrorCode.PERMISSION_DENIED: RepairAction.STOP,
        ErrorCode.USER_CANCELLED: RepairAction.STOP,
    }

    def __init__(self) -> None:
        self._repair_counts: dict[str, int] = {}
        self._max_repairs_per_stage = 2
        self._max_repairs_total = 5

    def route(self, error: DBAideError, stage: str) -> RepairAction:
        """Determine repair action for an error."""
        action = self.REPAIR_MAP.get(error.code, RepairAction.STOP)

        # Check repair budget
        stage_key = f"{stage}:{action.value}"
        stage_count = self._repair_counts.get(stage_key, 0)
        total_count = sum(self._repair_counts.values())

        if stage_count >= self._max_repairs_per_stage:
            logger.warning("repair budget exhausted for stage %s", stage)
            return RepairAction.STOP

        if total_count >= self._max_repairs_total:
            logger.warning("total repair budget exhausted")
            return RepairAction.STOP

        # Record repair attempt
        self._repair_counts[stage_key] = stage_count + 1

        return action

    def reset(self) -> None:
        """Reset repair counters for a new workflow."""
        self._repair_counts.clear()

    def should_retry(self, error: DBAideError) -> bool:
        """Check if error is retryable."""
        return error.retryable and error.code in {
            ErrorCode.SQL_EXECUTION_FAILED,
            ErrorCode.QUERY_TIMEOUT,
            ErrorCode.LLM_ERROR,
            ErrorCode.CONNECTION_FAILED,
        }
