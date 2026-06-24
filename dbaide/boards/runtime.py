"""Execute a :class:`ParametricChart` recipe — pure Python, no model call.

bind params (type-strict) → run each read-only SQL → combine the result sets per
the recipe → materialize via the existing ``ChartAgent.build_spec`` (the same
deterministic field→role transform used for normal charts). The executor is
injected so this is testable without a database and reusable from the desktop
service or the WebChannel bridge.

Safety: ``:param`` placeholders are replaced only for *declared* params, and each
value is validated + rendered per its declared type (numbers parsed, dates ISO
-checked, enums constrained to their allow-list, text single-quote-escaped). The
read-only engine + ``validate_sql_report`` remain the hard backstops upstream.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from dbaide.agent.chart_agent import ChartAgent, chart_plan_from_dict
from dbaide.boards.parametric import Combine, ParamSpec, ParametricChart, QuerySource
from dbaide.boards.refresh import _rows_as_dicts
from dbaide.charts.spec import chart_spec_to_dict

# execute_sql(sql:str) -> {"columns":[...], "rows":[...], "row_count":int}
ExecuteSql = Callable[[str], dict[str, Any]]


def _render_literal(spec: ParamSpec, value: Any) -> str:
    if value is None:
        value = spec.default
    if value is None:
        return "NULL"
    if spec.type == "number":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"param {spec.name!r} expects a number, got {value!r}")
        return str(int(f)) if f.is_integer() else repr(f)
    if spec.type == "date":
        s = str(value)
        try:
            date.fromisoformat(s)
        except ValueError:
            raise ValueError(f"param {spec.name!r} expects an ISO date (YYYY-MM-DD), got {value!r}")
        return "'" + s + "'"
    if spec.type == "enum":
        if spec.options and value not in spec.options:
            raise ValueError(f"param {spec.name!r}={value!r} is not in the allowed options")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"   # text


def render_sql(template: str, values: dict[str, Any], params: list[ParamSpec]) -> str:
    """Replace declared ``:param`` tokens in *template* with type-checked literals."""
    if not params:
        return template
    by_name = {p.name: p for p in params}
    pattern = re.compile(r":(" + "|".join(re.escape(p.name) for p in params) + r")\b")
    return pattern.sub(lambda m: _render_literal(by_name[m.group(1)], values.get(m.group(1), by_name[m.group(1)].default)), template)


def combine_rows(result_sets: list[tuple[QuerySource, list[dict[str, Any]]]], combine: Combine) -> list[dict[str, Any]]:
    """Merge per-source rows into the single row set the chart plan consumes."""
    sets = list(result_sets)
    if not sets:
        return []
    if combine.mode == "single" or len(sets) == 1:
        return [dict(r) for r in sets[0][1]]
    if combine.mode == "union":
        out: list[dict[str, Any]] = []
        for src, rows in sets:
            tag = src.label or src.id
            for r in rows:
                rr = dict(r)
                if combine.tag_field:
                    rr[combine.tag_field] = tag
                out.append(rr)
        return out
    if combine.mode == "join" and combine.key:
        key = combine.key
        index: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        for src, rows in sets:
            for r in rows:
                k = r.get(key)
                if k not in index:
                    index[k] = {}
                    order.append(k)
                index[k].update(r)   # merge columns on the shared key (later sources win)
        return [index[k] for k in order]
    # join without a key, or unknown mode → fall back to first source
    return [dict(r) for r in sets[0][1]]


def run_parametric_chart(chart: ParametricChart, param_values: dict[str, Any], execute_sql: ExecuteSql) -> dict[str, Any]:
    """Bind params, run the source SQLs, combine, and materialize the chart spec."""
    values = {**chart.default_params(), **(param_values or {})}
    result_sets: list[tuple[QuerySource, list[dict[str, Any]]]] = []
    for src in chart.sources:
        sql = render_sql(src.sql, values, chart.params)
        res = execute_sql(sql)
        if res.get("pending_confirmation"):
            raise ValueError("a source query needs confirmation and cannot be auto-run")
        columns = [str(c) for c in (res.get("columns") or [])]
        result_sets.append((src, _rows_as_dicts(columns, res.get("rows"))))
    combined = combine_rows(result_sets, chart.combine)
    plan = chart_plan_from_dict(chart.chart_plan or {})
    spec = ChartAgent().build_spec(plan, chart_id=chart.chart_id or "chart", rows=combined)
    return {"chart_spec": chart_spec_to_dict(spec), "row_count": len(combined)}
