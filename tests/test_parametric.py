"""Parameterized-chart recipe model + deterministic runtime (no DB, no LLM)."""

from __future__ import annotations

import pytest

from dbaide.boards.parametric import Combine, ParametricChart, ParamSpec, QuerySource
from dbaide.boards.runtime import combine_rows, render_sql, run_parametric_chart


# -- model ------------------------------------------------------------------

def test_recipe_roundtrip():
    chart = ParametricChart(
        chart_id="c1", title="销售",
        sources=[QuerySource("s", "SELECT region, sum(amt) amt FROM sales WHERE month=:month GROUP BY 1")],
        params=[ParamSpec("month", "date", "月份", "2024-03")],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    )
    again = ParametricChart.from_dict(chart.to_dict())
    assert again.chart_id == "c1"
    assert again.sources[0].sql == chart.sources[0].sql
    assert again.params[0].type == "date"
    assert again.default_params() == {"month": "2024-03"}


# -- type-strict binding ----------------------------------------------------

def test_render_sql_binds_declared_params_by_type():
    params = [ParamSpec("day", "date"), ParamSpec("n", "number"),
              ParamSpec("kw", "text"), ParamSpec("cat", "enum", options=["A", "B"])]
    sql = "WHERE day >= :day AND qty > :n AND name LIKE :kw AND cat = :cat AND note = ':notparam'"
    out = render_sql(sql, {"day": "2024-03-01", "n": 5, "kw": "o'brien", "cat": "A"}, params)
    assert "day >= '2024-03-01'" in out
    assert "qty > 5" in out
    assert "name LIKE 'o''brien'" in out      # single quote escaped
    assert "cat = 'A'" in out
    assert "':notparam'" in out               # undeclared colon token left untouched


def test_render_sql_degrades_bad_values_to_null_never_raises():
    # a filter change must never crash the chart — bad/unknown values become NULL
    assert render_sql("x = :n", {"n": "notanumber"}, [ParamSpec("n", "number")]) == "x = NULL"
    assert render_sql("c = :c", {"c": "Z"}, [ParamSpec("c", "enum", options=["A", "B"])]) == "c = NULL"
    # date is escaped, not format-validated (a month or full date both render safely)
    assert render_sql("d = :d", {"d": "2024/03/01"}, [ParamSpec("d", "date")]) == "d = '2024/03/01'"


# -- combine ----------------------------------------------------------------

def test_combine_union_tags_each_source():
    s1, s2 = QuerySource("a", "", "今年"), QuerySource("b", "", "去年")
    rows = combine_rows([(s1, [{"m": "1", "v": 10}]), (s2, [{"m": "1", "v": 8}])],
                        Combine("union", tag_field="期间"))
    assert rows == [{"m": "1", "v": 10, "期间": "今年"}, {"m": "1", "v": 8, "期间": "去年"}]


def test_combine_join_merges_on_key():
    s1, s2 = QuerySource("sales", ""), QuerySource("target", "")
    rows = combine_rows([(s1, [{"m": "1", "sales": 10}, {"m": "2", "sales": 20}]),
                         (s2, [{"m": "1", "target": 12}, {"m": "2", "target": 18}])],
                        Combine("join", key="m"))
    assert rows == [{"m": "1", "sales": 10, "target": 12}, {"m": "2", "sales": 20, "target": 18}]


# -- end to end (no LLM) ----------------------------------------------------

def test_run_parametric_chart_is_deterministic():
    chart = ParametricChart(
        chart_id="c1", title="各区域销售额",
        sources=[QuerySource("s", "SELECT region, sum(amt) AS amt FROM sales WHERE month=:month GROUP BY 1")],
        params=[ParamSpec("month", "text", default="2024-03")],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "title": "各区域销售额",
                    "category_field": "region", "value_fields": ["amt"]},
    )
    seen = {}

    def fake_exec(sql):
        seen["sql"] = sql
        return {"columns": ["region", "amt"], "rows": [["华东", 30], ["华北", 12]], "row_count": 2}

    out = run_parametric_chart(chart, {"month": "2024-06"}, fake_exec)
    assert "month='2024-06'" in seen["sql"]                 # text param bound + quoted
    assert out["row_count"] == 2
    spec = out["chart_spec"]
    assert spec["chart_type"] == "bar"
    assert spec["categories"] == ["华东", "华北"]
    assert spec["series"][0]["values"] == [30.0, 12.0]


def test_run_parametric_chart_survives_edge_filter_values():
    # the user's report: changing a filter must not error. Month strings, empties,
    # and out-of-range values used to crash the bind; now they degrade gracefully.
    chart = ParametricChart(
        chart_id="c1", title="t",
        sources=[QuerySource("s", "SELECT region, sum(amt) AS amt FROM s WHERE month=:month "
                                  "AND region IN (:region) GROUP BY 1")],
        params=[ParamSpec("month", "date", default="@month_str"),
                ParamSpec("region", "enum", options=["华东", "华北"], multi=True)],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    )
    seen = {}

    def ex(sql):
        seen["sql"] = sql
        return {"columns": ["region", "amt"], "rows": [["华东", 1]]}

    # a month string (not a full ISO date) — used to raise, now binds escaped
    run_parametric_chart(chart, {"month": "2024-06", "region": ["华东"]}, ex)
    assert "month='2024-06'" in seen["sql"] and "IN ('华东')" in seen["sql"]
    # cleared month → falls back to default; unchecked regions → IN (NULL); no crash
    out = run_parametric_chart(chart, {"month": "", "region": []}, ex)
    assert "IN (NULL)" in seen["sql"]
    assert out["chart_spec"]["chart_type"] == "bar"


def test_run_parametric_chart_reconciles_mismatched_plan():
    # THE failure: chart_plan fields drifted from the SQL columns → it rendered garbage
    # (all "—"/0). Reconciliation maps the plan onto the real columns so it renders.
    chart = ParametricChart(
        chart_id="c", title="t", sources=[QuerySource("s", "SELECT 1")], params=[],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]})
    out = run_parametric_chart(chart, {},
                               lambda _s: {"columns": ["城市", "销售额"], "rows": [["广州", 100], ["成都", 80]]})
    s = out["chart_spec"]
    assert s["categories"] == ["广州", "成都"]            # category auto-derived to the text column
    assert s["series"][0]["values"] == [100.0, 80.0]      # values auto-derived to the numeric column
    # a CORRECT plan is left untouched
    chart2 = ParametricChart(
        chart_id="c", title="t", sources=[QuerySource("s", "SELECT 1")], params=[], combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "城市", "value_fields": ["销售额"]})
    out2 = run_parametric_chart(chart2, {}, lambda _s: {"columns": ["城市", "销售额"], "rows": [["广州", 5]]})
    assert out2["chart_spec"]["categories"] == ["广州"]


def test_combine_join_drops_null_key_rows():
    from dbaide.boards.runtime import combine_rows
    s1, s2 = QuerySource("a", ""), QuerySource("b", "")
    sets = [(s1, [{"k": 1, "x": 10}, {"k": None, "x": 99}]),
            (s2, [{"k": 1, "y": 20}, {"k": 2, "y": 30}])]
    out = combine_rows(sets, Combine(mode="join", key="k"))
    assert sorted(r["k"] for r in out) == [1, 2]          # the None-key row is dropped, not bucketed
    merged = next(r for r in out if r["k"] == 1)
    assert merged["x"] == 10 and merged["y"] == 20        # real keys still join correctly


def test_run_parametric_chart_empty_rows_is_no_data_not_error():
    # THE bug: a filter that matched nothing made build_spec().validate() raise
    # "each series requires non-empty values" → the chart errored on every such change.
    chart = ParametricChart(
        chart_id="c1", title="t",
        sources=[QuerySource("s", "SELECT region, sum(amt) AS amt FROM s WHERE month=:m GROUP BY 1")],
        params=[ParamSpec("m", "date", default="@month_str")],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    )
    out = run_parametric_chart(chart, {"m": "2099-01"}, lambda _sql: {"columns": ["region", "amt"], "rows": []})
    assert out["chart_spec"] is None and out["row_count"] == 0   # no rows → "no data", never raises


def test_run_parametric_chart_rows_are_json_numeric():
    # Decimal/date DB cells must come back as JSON-native types so the page's KPI
    # numeric detection + formatting work (default=str would stringify them).
    from datetime import date as _date
    from decimal import Decimal
    chart = ParametricChart(
        chart_id="c1", title="t",
        sources=[QuerySource("s", "SELECT d, total FROM s")],
        params=[], combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "d", "value_fields": ["total"]},
    )
    out = run_parametric_chart(chart, {}, lambda _sql: {
        "columns": ["d", "total"], "rows": [[_date(2024, 6, 1), Decimal("1234.50")]]})
    row = out["rows"][0]
    assert row["total"] == 1234.5 and isinstance(row["total"], float)   # Decimal → float
    assert row["d"] == "2024-06-01"                                      # date → ISO string


def test_run_parametric_chart_multi_sql_join():
    chart = ParametricChart(
        chart_id="c2", title="销售 vs 目标",
        sources=[QuerySource("sales", "SELECT m, sales FROM s WHERE y=:y"),
                 QuerySource("target", "SELECT m, target FROM t WHERE y=:y")],
        params=[ParamSpec("y", "number", default=2024)],
        combine=Combine("join", key="m"),
        chart_plan={"chart_type": "combo", "category_field": "m", "value_fields": ["sales", "target"]},
    )
    calls = []

    def fake_exec(sql):
        calls.append(sql)
        if "FROM s" in sql:
            return {"columns": ["m", "sales"], "rows": [["1月", 10], ["2月", 20]]}
        return {"columns": ["m", "target"], "rows": [["1月", 12], ["2月", 18]]}

    out = run_parametric_chart(chart, {}, fake_exec)        # uses default y=2024
    assert all("y=2024" in c for c in calls)               # numeric param, no quotes
    assert set(out["chart_spec"]["categories"]) == {"1月", "2月"}   # order is a chart-sort concern
    names = {s["name"] for s in out["chart_spec"]["series"]}
    assert {"sales", "target"} <= names                    # both joined sources became series
