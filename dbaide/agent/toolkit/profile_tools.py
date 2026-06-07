"""Profiling / column-stats tools."""
from __future__ import annotations

from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import PROFILE_TABLE, COLUMN_STATS
from dbaide.agent.toolkit.support import _err, _normalize_tool_table, _string_list


def register(registry: ToolRegistry, orchestrator) -> None:
    def _profile_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator.run_state.table or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not table:
            return ToolResult(ok=False, error=_err("profile_table", "table is required"))
        database, table = _normalize_tool_table(orchestrator, table, database)
        columns = args.get("columns")
        if not columns:
            cols = orchestrator.schema.describe_table(table, database=database)
            if not cols:
                target = f"{database}.{table}" if database else table
                return ToolResult(ok=False, error=_err("profile_table", f"table not found or has no readable columns: {target}"))
            columns = [c.name for c in cols[:8]]
        else:
            columns = _string_list(columns)
        profiles = orchestrator.profile.profile_table(table, list(columns), database=database)
        answer = orchestrator.formatter.profiles(profiles, language=orchestrator.run_state.answer_language)
        orchestrator.run_state.answer = answer
        return ToolResult(
            ok=True,
            data={
                "table": table,
                "database": database,
                "answer": answer,
                "column_count": len(profiles),
                "profiles": [_profile_to_dict(profile) for profile in profiles],
            },
        )

    def _column_stats(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator.run_state.table or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not table:
            return ToolResult(ok=False, error=_err("column_stats", "table is required"))
        database, table = _normalize_tool_table(orchestrator, table, database)
        columns = _string_list(args.get("columns")) or None
        metrics = _string_list(args.get("metrics")) or None
        try:
            stats = orchestrator.profile.column_stats(
                table, columns, metrics=metrics, database=database,
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("column_stats", str(exc), retryable=True))
        if not stats:
            target = f"{database}.{table}" if database else table
            return ToolResult(ok=False, error=_err("column_stats", f"no matching columns for {target}"))
        return ToolResult(ok=True, data={"table": table, "database": database, "columns": stats})

    registry.register(PROFILE_TABLE, _profile_table)
    registry.register(COLUMN_STATS, _column_stats)


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    return {
        "table": getattr(profile, "table", ""),
        "column": getattr(profile, "column", ""),
        "row_count": getattr(profile, "row_count", 0),
        "null_count": getattr(profile, "null_count", 0),
        "distinct_count": getattr(profile, "distinct_count", None),
        "min_value": getattr(profile, "min_value", None),
        "max_value": getattr(profile, "max_value", None),
        "top_values": list(getattr(profile, "top_values", []) or [])[:10],
        "sample_values": list(getattr(profile, "sample_values", []) or [])[:10],
        "data_kind": getattr(profile, "data_kind", ""),
        "null_rate": getattr(profile, "null_rate", None),
        "distinct_ratio": getattr(profile, "distinct_ratio", None),
    }
