"""Deterministic refresh for a saved question.

Re-run the saved SQL and rebuild the chart from the saved plan — no model call.
Kept pure (the executor is injected) so it is testable without a live database
and reusable from both the desktop service and any headless caller.
"""

from __future__ import annotations

from typing import Any, Callable

from dbaide.agent.chart_agent import ChartAgent, chart_plan_from_dict
from dbaide.boards.models import SavedQuestion
from dbaide.charts.spec import chart_spec_to_dict

# execute_sql(connection_name, database, sql) -> {"columns": [...], "rows": [...], "row_count": int}
ExecuteSql = Callable[..., dict[str, Any]]


def _rows_as_dicts(columns: list[str], rows: Any) -> list[dict[str, Any]]:
    """Normalise a result's rows to a list of column-keyed dicts.

    ``execute_sql`` may return rows as dicts already or as positional
    lists/tuples; charting wants dicts keyed by column name."""
    out: list[dict[str, Any]] = []
    cols = [str(c) for c in (columns or [])]
    for r in rows or []:
        if isinstance(r, dict):
            out.append(dict(r))
        elif isinstance(r, (list, tuple)):
            out.append({cols[i] if i < len(cols) else str(i): v for i, v in enumerate(r)})
    return out


def refresh_question(question: SavedQuestion, execute_sql: ExecuteSql) -> dict[str, Any]:
    """Re-run *question*'s SQL and rebuild its chart from the saved plan.

    Returns ``{"chart_spec", "columns", "row_count"}``. Raises ``ValueError`` if
    the question isn't refreshable (no SQL/plan) or the query needs confirmation."""
    if not question.refreshable:
        raise ValueError("question is not refreshable (missing sql or chart_plan)")
    res = execute_sql(
        connection_name=question.connection_name,
        database=question.database,
        sql=question.sql,
    )
    if res.get("pending_confirmation"):
        raise ValueError("saved query needs confirmation and cannot be auto-refreshed")
    columns = [str(c) for c in (res.get("columns") or [])]
    rows = _rows_as_dicts(columns, res.get("rows"))
    plan = chart_plan_from_dict(question.chart_plan or {})
    spec = ChartAgent().build_spec(plan, chart_id=question.id, rows=rows)
    return {
        "chart_spec": chart_spec_to_dict(spec),
        "columns": columns,
        "row_count": int(res.get("row_count") or len(rows)),
    }
