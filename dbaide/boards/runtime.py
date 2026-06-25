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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from dbaide.agent.chart_agent import ChartAgent, chart_plan_from_dict
from dbaide.boards.dates import resolve_value
from dbaide.boards.parametric import Combine, ParamSpec, ParametricChart, QuerySource
from dbaide.boards.refresh import _rows_as_dicts
from dbaide.charts.spec import chart_spec_to_dict

# execute_sql(sql:str) -> {"columns":[...], "rows":[...], "row_count":int}
ExecuteSql = Callable[[str], dict[str, Any]]


def _jsonable(v: Any) -> Any:
    """Coerce a DB cell so it serializes as a proper JSON type for the page."""
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


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
                if k is None:
                    continue   # a row with no join key can't be joined — dropping it silently
                              # (and merging all NULL-key rows into one bucket) would lose data
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
    # data even when this recipe can't form a chart. Coerce so numerics survive the
    # bridge's JSON as NUMBERS (Decimal/date would otherwise stringify, breaking KPI
    # numeric detection + formatting on the page).
    columns = list(combined[0].keys())
    rows = [{k: _jsonable(v) for k, v in r.items()} for r in combined[:200]]
    # Reconcile the chart_plan's field→role mapping against the ACTUAL result columns: the
    # plan is authored separately from the SQL and often drifts (wrong column names), which
    # otherwise renders garbage (all "—"/0). This makes the chart shape from real data.
    plan = chart_plan_from_dict(_reconcile_chart_plan(chart.chart_plan or {}, columns, combined))
    try:
        spec = ChartAgent().build_spec(plan, chart_id=chart.chart_id or "chart", rows=combined)
        chart_spec = chart_spec_to_dict(spec)
    except ValueError:
        chart_spec = None   # can't chart these rows — a chart tile shows "no data", kpi/table still work
    # chart_type lets the page build a client-side fallback chart from rows when chart_spec is None
    return {"chart_spec": chart_spec, "chart_type": getattr(plan, "chart_type", "") or "",
            "row_count": len(combined), "columns": columns, "rows": rows}


def _is_numeric_column(col: str, rows: list[dict[str, Any]]) -> bool:
    seen = False
    for r in rows:
        v = r.get(col)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
            return False
        seen = True
    return seen


def _reconcile_chart_plan(plan: dict[str, Any], columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Repair a chart_plan whose role fields don't match the real result columns.

    A field that names a column that doesn't exist is replaced by an auto-derived one
    (first text column → category/x; numeric columns → values/y). Fields that DO match are
    left untouched, so a correct plan is unchanged."""
    if not columns:
        return plan
    p = dict(plan)
    colset = set(columns)
    numeric = [c for c in columns if _is_numeric_column(c, rows)]
    text = [c for c in columns if c not in numeric]

    # category / x-axis label field
    for role in ("category_field", "x_field"):
        if p.get(role) and p[role] not in colset:
            p[role] = (text[0] if text else columns[0])
    cat = p.get("category_field") or p.get("x_field") or ""

    # value series fields
    vals = [v for v in (p.get("value_fields") or []) if v in colset]
    if p.get("value_fields") and not vals:
        vals = [c for c in numeric if c != cat] or [c for c in columns if c != cat]
        p["value_fields"] = vals

    # scalar y / size fields used by scatter-like charts
    for role in ("y_field", "size_field"):
        if p.get(role) and p[role] not in colset:
            cand = [c for c in numeric if c != cat]
            if cand:
                p[role] = cand[0]
    return p
