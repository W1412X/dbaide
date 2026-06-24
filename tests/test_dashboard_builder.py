"""Conversational dashboard-builder agent: HTML + runnable recipes (mock LLM)."""

from __future__ import annotations

import pytest

from dbaide.agent.dashboard_builder import DashboardBuilderAgent
from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.boards.parametric import ParametricChart, ParametricDashboard
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


_PAYLOAD = {
    "name": "销售看板",
    "html": ('<div class="bar"><label>月份</label>'
             '<input data-param="month" value="2024-06"><button data-apply>应用</button></div>'
             '<div data-chart="c1" style="height:280px"></div>'),
    "charts": [{
        "chart_id": "c1", "title": "各区域销售额",
        "sources": [{"id": "main", "sql": "SELECT region, sum(amt) AS amt FROM sales WHERE month=:month GROUP BY 1"}],
        "params": [{"name": "month", "type": "text", "default": "@month_str"}],
        "combine": {"mode": "single"},
        "chart_plan": {"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    }],
}


def test_builder_produces_html_and_runnable_recipes():
    validated = []
    app = DashboardBuilderAgent(_MockLLM(_PAYLOAD)).build(
        instruction="做个销售看板", context_charts=[], connection_name="shop",
        validate=lambda sql: (validated.append(sql), _ok(sql))[1])
    assert app.name == "销售看板" and app.connection_name == "shop"
    assert 'data-chart="c1"' in app.html and 'data-param="month"' in app.html
    assert app.charts[0].chart_id == "c1"
    assert validated and ":month" not in validated[0]      # validated a fully-bound SELECT
    # the recipe actually runs through the deterministic runtime
    out = run_parametric_chart(app.charts[0], {"month": "2024-06"},
                               lambda sql: {"columns": ["region", "amt"], "rows": [["华东", 9], ["华北", 4]]})
    assert out["chart_spec"]["categories"] == ["华东", "华北"]


def test_builder_refine_feeds_existing_to_the_model():
    existing = ParametricDashboard(
        "旧看板", "shop", html="<old/>",
        charts=[ParametricChart.from_dict(_PAYLOAD["charts"][0])])
    llm = _MockLLM({**_PAYLOAD, "name": "新看板"})
    app = DashboardBuilderAgent(llm).build(instruction="加个饼图", existing=existing, connection_name="shop")
    assert app.id == existing.id and app.name == "新看板"   # same app, updated in place
    user_msg = llm.seen[-1].content
    assert "CURRENT dashboard" in user_msg and "<old/>" in user_msg   # existing passed for refinement


def test_builder_requires_llm():
    with pytest.raises(ModelRequiredError):
        DashboardBuilderAgent().build(instruction="x")


def test_builder_synthesizes_a_body_when_html_is_empty():
    # empty/garbled HTML no longer fails — a clean layout is generated from the recipes
    app = DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "html": ""})).build(instruction="x")
    from dbaide.rendering.dashboard_body import chart_container_ids
    assert chart_container_ids(app.html) == {c.chart_id for c in app.charts}


def test_builder_still_rejects_no_charts():
    with pytest.raises(ValueError):
        DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "charts": []})).build(instruction="x")


def test_builder_rejects_non_readonly_sql():
    bad = {**_PAYLOAD, "charts": [{**_PAYLOAD["charts"][0],
                                   "sources": [{"id": "m", "sql": "DROP TABLE sales"}]}]}
    with pytest.raises(ValueError, match="validation"):
        DashboardBuilderAgent(_MockLLM(bad)).build(
            instruction="x", validate=lambda sql: type("R", (), {"ok": False, "issues": ["not read-only"]})())
