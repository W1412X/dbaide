"""Dashboard-compiler agent: fixed SQL + chart plan → parameterized recipe (mock LLM)."""

from __future__ import annotations

import pytest

from dbaide.agent.dashboard_compiler import DashboardCompiler
from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.boards.runtime import run_parametric_chart
from dbaide.llm import LLMClient, LLMMessage


class _MockLLM(LLMClient):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.seen: list = []

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        self.seen = messages
        return dict(self.payload)


def _ok(_sql):
    return type("R", (), {"ok": True, "issues": []})()


def test_compile_produces_runnable_recipe():
    llm = _MockLLM({
        "sources": [{"id": "main",
                     "sql": "SELECT region, sum(amt) AS amt FROM sales WHERE month=:month GROUP BY 1",
                     "label": ""}],
        "params": [{"name": "month", "type": "text", "label": "月份", "default": "@month_str"}],
        "combine": {"mode": "single"},
    })
    validated = []
    chart = DashboardCompiler(llm).compile_chart(
        chart_id="c1", title="各区域销售额",
        source_sql="SELECT region, sum(amt) AS amt FROM sales GROUP BY 1",
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
        nl_question="各区域销售额",
        validate=lambda sql: (validated.append(sql), _ok(sql))[1],
    )
    assert chart.chart_id == "c1" and chart.title == "各区域销售额"
    assert chart.params[0].name == "month"
    assert chart.chart_plan["category_field"] == "region"     # reused, not re-derived
    # the validator saw a fully-bound (no :param) SELECT
    assert validated and ":month" not in validated[0]
    # the recipe actually runs deterministically through the runtime
    out = run_parametric_chart(chart, {"month": "2024-06"},
                               lambda sql: {"columns": ["region", "amt"], "rows": [["华东", 9], ["华北", 4]]})
    assert out["chart_spec"]["categories"] == ["华东", "华北"]


def test_compile_rejects_sql_that_fails_validation():
    llm = _MockLLM({
        "sources": [{"id": "m", "sql": "DELETE FROM t WHERE id=:x"}],
        "params": [{"name": "x", "type": "number", "default": 1}],
        "combine": {"mode": "single"},
    })
    bad = lambda sql: type("R", (), {"ok": False, "issues": ["not a read-only SELECT"]})()
    with pytest.raises(ValueError, match="validation"):
        DashboardCompiler(llm).compile_chart(
            chart_id="c", title="t", source_sql="SELECT 1",
            chart_plan={"chart_type": "bar"}, validate=bad)


def test_compile_requires_an_llm():
    with pytest.raises(ModelRequiredError):
        DashboardCompiler().compile_chart(chart_id="c", title="t", source_sql="SELECT 1", chart_plan={})


def test_compile_rejects_empty_sources():
    llm = _MockLLM({"sources": [], "params": [], "combine": {"mode": "single"}})
    with pytest.raises(ValueError, match="no SQL sources"):
        DashboardCompiler(llm).compile_chart(chart_id="c", title="t", source_sql="SELECT 1", chart_plan={})
