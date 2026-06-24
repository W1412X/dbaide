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
from dbaide.boards.dates import resolve_value
from dbaide.boards.parametric import Combine, ParamSpec, ParametricChart, QuerySource
from dbaide.boards.refresh import _rows_as_dicts
from dbaide.charts.spec import chart_spec_to_dict

# execute_sql(sql:str) -> {"columns":[...], "rows":[...], "row_count":int}
ExecuteSql = Callable[[str], dict[str, Any]]


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _render_one(spec: ParamSpec, value: Any) -> str:
    """Render a single value as a SAFE SQL literal.

    Robustness-first: a control can send anything (empty, a month string, a typo),
    and a filter change must never hard-error. Bad/empty values render as NULL
    (so the filter matches nothing rather than crashing); everything else is
    single-quote-escaped (injection-safe) — the read-only engine is the backstop.
    """
    if _is_blank(value):
        return "NULL"
    if spec.type == "number":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "NULL"                       # bad number → NULL, never crash the chart
        return str(int(f)) if f.is_integer() else repr(f)
    if spec.type == "enum" and spec.options and value not in spec.options:
        return "NULL"                           # constrain to the allow-list (no match, no crash)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    # text / date / enum-value → escaped quoted literal. Dates are NOT format-validated
    # here (a "2024-06" month or a full date both work); escaping keeps it injection-safe.
    return "'" + str(value).replace("'", "''") + "'"


def _render_param(spec: ParamSpec, value: Any, today: date | None) -> str:
    """Render a param value: empty → default, resolve @tokens, expand multi → IN list."""
    if _is_blank(value):
        value = spec.default                    # cleared control → fall back to the default
    if spec.multi:
        raw = value if isinstance(value, (list, tuple)) else ([] if _is_blank(value) else [value])
        rendered = [_render_one(spec, resolve_value(v, today)) for v in raw if not _is_blank(v)]
        rendered = [r for r in rendered if r != "NULL"]   # drop blanks/out-of-options from IN(...)
        return ", ".join(rendered) if rendered else "NULL"
    return _render_one(spec, resolve_value(value, today))


def render_sql(template: str, values: dict[str, Any], params: list[ParamSpec],
               today: date | None = None) -> str:
    """Replace declared ``:param`` tokens in *template* with type-checked literals.

    Only declared param names are substituted; dynamic ``@`` defaults are resolved
    against *today* (defaults to the real date)."""
    if not params:
        return template
    by_name = {p.name: p for p in params}
    pattern = re.compile(r":(" + "|".join(re.escape(p.name) for p in params) + r")\b")
    return pattern.sub(
        lambda m: _render_param(by_name[m.group(1)], values.get(m.group(1), by_name[m.group(1)].default), today),
        template,
    )


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


def run_parametric_chart(chart: ParametricChart, param_values: dict[str, Any], execute_sql: ExecuteSql,
                         *, today: date | None = None) -> dict[str, Any]:
    """Bind params, run the source SQLs, combine, and materialize the chart spec."""
    values = {**chart.default_params(), **(param_values or {})}
    result_sets: list[tuple[QuerySource, list[dict[str, Any]]]] = []
    for src in chart.sources:
        sql = render_sql(src.sql, values, chart.params, today=today)
        res = execute_sql(sql)
        if res.get("pending_confirmation"):
            raise ValueError("a source query needs confirmation and cannot be auto-run")
        columns = [str(c) for c in (res.get("columns") or [])]
        result_sets.append((src, _rows_as_dicts(columns, res.get("rows"))))
    combined = combine_rows(result_sets, chart.combine)
    if not combined:
        return {"chart_spec": None, "row_count": 0, "columns": [], "rows": []}   # matched nothing → "no data"
    # rows/columns are returned too, so kpi and table tiles can render straight from the
    # data even when this recipe can't form a chart
    columns = list(combined[0].keys())
    rows = [dict(r) for r in combined[:200]]
    plan = chart_plan_from_dict(chart.chart_plan or {})
    try:
        spec = ChartAgent().build_spec(plan, chart_id=chart.chart_id or "chart", rows=combined)
        chart_spec = chart_spec_to_dict(spec)
    except ValueError:
        chart_spec = None   # can't chart these rows — a chart tile shows "no data", kpi/table still work
    return {"chart_spec": chart_spec, "row_count": len(combined), "columns": columns, "rows": rows}
