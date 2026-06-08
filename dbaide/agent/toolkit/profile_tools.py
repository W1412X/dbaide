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
        explicit = _string_list(args.get("columns"))
        offset = max(0, _int(args.get("column_offset"), 0))
        # Profiling scans the table per column, so an unbounded auto-profile is costly;
        # window the columns but report the total and how to page so none is hidden.
        if explicit:
            columns, total_columns, more = explicit, len(explicit), False
        else:
            cols = orchestrator.schema.describe_table(table, database=database)
            if not cols:
                target = f"{database}.{table}" if database else table
                return ToolResult(ok=False, error=_err("profile_table", f"table not found or has no readable columns: {target}"))
            all_names = [c.name for c in cols]
            total_columns = len(all_names)
            limit = max(1, _int(args.get("column_limit"), 8))
            columns = all_names[offset:offset + limit]
            more = (offset + len(columns)) < total_columns
        profiles = orchestrator.profile.profile_table(table, list(columns), database=database)
        answer = orchestrator.formatter.profiles(profiles, language=orchestrator.run_state.answer_language)
        orchestrator.run_state.answer = answer
        data: dict[str, Any] = {
            "table": table,
            "database": database,
            "answer": answer,
            "column_count": len(profiles),
            "total_columns": total_columns,
            "column_offset": offset if not explicit else 0,
            "more_columns": more,
            "profiles": [_profile_to_dict(profile) for profile in profiles],
        }
        if more:
            data["note"] = (
                f"Profiled columns {offset + 1}–{offset + len(columns)} of {total_columns}. "
                f"Pass column_offset={offset + len(columns)} for the next page, or list explicit "
                f"`columns` — the un-profiled columns were NOT computed."
            )
        return ToolResult(ok=True, data=data)

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


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
