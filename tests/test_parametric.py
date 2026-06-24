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


def test_render_sql_rejects_bad_types_and_enum():
    with pytest.raises(ValueError):
        render_sql("x = :n", {"n": "notanumber"}, [ParamSpec("n", "number")])
    with pytest.raises(ValueError):
        render_sql("d = :d", {"d": "2024/03/01"}, [ParamSpec("d", "date")])
    with pytest.raises(ValueError):
        render_sql("c = :c", {"c": "Z"}, [ParamSpec("c", "enum", options=["A", "B"])])


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
