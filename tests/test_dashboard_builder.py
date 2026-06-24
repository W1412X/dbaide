"""Conversational dashboard-builder agent: declarative layout + runnable recipes (mock LLM)."""

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
    "layout": {"rows": [
        {"tiles": [{"kind": "kpi", "chart": "c1", "span": 3, "label": "销售额"},
                   {"kind": "chart", "chart": "c1", "span": 9, "title": "各区域销售额"}]},
    ]},
    "charts": [{
        "chart_id": "c1", "title": "各区域销售额",
        "sources": [{"id": "main", "sql": "SELECT region, sum(amt) AS amt FROM sales WHERE month=:month GROUP BY 1"}],
        "params": [{"name": "month", "type": "text", "default": "@month_str"}],
        "combine": {"mode": "single"},
        "chart_plan": {"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    }],
}


def test_builder_renders_layout_and_runnable_recipes():
    validated = []
    app = DashboardBuilderAgent(_MockLLM(_PAYLOAD)).build(
        instruction="做个销售看板", context_charts=[], connection_name="shop",
        validate=lambda sql: (validated.append(sql), _ok(sql))[1])
    assert app.name == "销售看板" and app.connection_name == "shop"
    assert app.layout and app.layout[0]["tiles"][0]["kind"] == "kpi"   # structured layout kept
    # the system renders that layout into a themed body (no model HTML)
    assert 'data-chart="c1"' in app.html and 'data-param="month"' in app.html
    assert 'data-kind="kpi"' in app.html and "dbaide-row" in app.html
    assert "<script" not in app.html.lower()
    assert validated and ":month" not in validated[0]                  # validated a fully-bound SELECT
    out = run_parametric_chart(app.charts[0], {"month": "2024-06"},
                               lambda sql: {"columns": ["region", "amt"], "rows": [["华东", 9], ["华北", 4]]})
    assert out["chart_spec"]["categories"] == ["华东", "华北"]


def test_builder_refine_feeds_existing_layout_to_the_model():
    existing = ParametricDashboard(
        "旧看板", "shop", layout=[{"tiles": [{"kind": "chart", "chart": "c1", "span": 12}]}],
        charts=[ParametricChart.from_dict(_PAYLOAD["charts"][0])])
    llm = _MockLLM({**_PAYLOAD, "name": "新看板"})
    app = DashboardBuilderAgent(llm).build(instruction="加个饼图", existing=existing, connection_name="shop")
    assert app.id == existing.id and app.name == "新看板"   # same app, updated in place
    user_msg = llm.seen[-1].content
    assert "CURRENT dashboard" in user_msg and '"layout"' in user_msg   # existing layout passed for refinement


def test_builder_requires_llm():
    with pytest.raises(ModelRequiredError):
        DashboardBuilderAgent().build(instruction="x")


def test_builder_falls_back_to_auto_grid_when_layout_missing():
    # a missing/garbled layout no longer fails — the system renders an auto-grid of recipes
    app = DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "layout": None})).build(instruction="x")
    assert 'data-chart="c1"' in app.html and "dbaide-grid" in app.html


def test_builder_still_rejects_no_charts():
    with pytest.raises(ValueError):
        DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "charts": []})).build(instruction="x")


def test_builder_rejects_non_readonly_sql():
    bad = {**_PAYLOAD, "charts": [{**_PAYLOAD["charts"][0],
                                   "sources": [{"id": "m", "sql": "DROP TABLE sales"}]}]}
    with pytest.raises(ValueError, match="validation"):
        DashboardBuilderAgent(_MockLLM(bad)).build(
            instruction="x", validate=lambda sql: type("R", (), {"ok": False, "issues": ["not read-only"]})())
