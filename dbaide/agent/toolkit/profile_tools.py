"""Profiling / column-stats tools."""
from __future__ import annotations

from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import PROFILE_TABLE, COLUMN_STATS
from dbaide.agent.schema_context import normalize_db_table
from dbaide.agent.toolkit.support import _err


def register(registry: ToolRegistry, orchestrator) -> None:
    def _profile_table(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator.run_state.table or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not table:
            return ToolResult(ok=False, error=_err("profile_table", "table is required"))
        database, table = normalize_db_table(table, database)
        columns = args.get("columns")
        if not columns:
            cols = orchestrator.schema.describe_table(table, database=database)
            if not cols:
                target = f"{database}.{table}" if database else table
                return ToolResult(ok=False, error=_err("profile_table", f"table not found or has no readable columns: {target}"))
            columns = [c.name for c in cols[:8]]
        profiles = orchestrator.profile.profile_table(table, list(columns), database=database)
        answer = orchestrator.formatter.profiles(profiles)
        orchestrator.run_state.answer = answer
        return ToolResult(ok=True, data={"answer": answer, "column_count": len(profiles)})

    def _column_stats(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        table = str(args.get("table") or orchestrator.run_state.table or "").strip()
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        if not table:
            return ToolResult(ok=False, error=_err("column_stats", "table is required"))
        database, table = normalize_db_table(table, database)
        columns = args.get("columns") if isinstance(args.get("columns"), list) else None
        metrics = args.get("metrics") if isinstance(args.get("metrics"), list) else None
        try:
            stats = orchestrator.profile.column_stats(
                table, columns, metrics=metrics, database=database,
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("column_stats", str(exc), retryable=True))
        if not stats:
            target = f"{database}.{table}" if database else table
            return ToolResult(ok=False, error=_err("column_stats", f"no matching columns for {target}"))
        return ToolResult(ok=True, data={"table": table, "columns": stats})

    registry.register(PROFILE_TABLE, _profile_table)
    registry.register(COLUMN_STATS, _column_stats)
