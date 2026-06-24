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


class _SeqLLM(LLMClient):
    """Returns a different payload on each call (to exercise the repair loop)."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls = 0
        self.seen: list = []

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        self.seen = messages
        p = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return dict(p)


def _ok(_sql):
    return type("R", (), {"ok": True, "issues": []})()


_PAYLOAD = {
    "name": "销售看板",
    "ui": {"type": "page", "children": [
        {"type": "row", "children": [
            {"type": "kpi", "chart": "c1", "span": 3, "label": "销售额"},
            {"type": "chart", "chart": "c1", "span": 9, "title": "各区域销售额"}]},
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
    assert app.layout["children"][0]["children"][0]["type"] == "kpi"   # component tree kept
    # the system renders that tree into a themed body (no model HTML)
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
    assert "CURRENT dashboard" in user_msg and '"ui"' in user_msg   # existing tree passed for refinement


def test_builder_requires_llm():
    with pytest.raises(ModelRequiredError):
        DashboardBuilderAgent().build(instruction="x")


def test_builder_falls_back_to_auto_grid_when_layout_missing():
    # a missing/garbled tree no longer fails — the system renders an auto-grid of recipes
    app = DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "ui": None})).build(instruction="x")
    assert 'data-chart="c1"' in app.html and "dbaide-grid" in app.html


def test_builder_still_rejects_no_charts():
    with pytest.raises(ValueError):
        DashboardBuilderAgent(_MockLLM({**_PAYLOAD, "charts": []})).build(instruction="x")


def test_builder_raises_when_recipes_never_validate():
    bad = {**_PAYLOAD, "charts": [{**_PAYLOAD["charts"][0],
                                   "sources": [{"id": "m", "sql": "DROP TABLE sales"}]}]}
    with pytest.raises(ValueError, match="fail against the database"):
        DashboardBuilderAgent(_MockLLM(bad)).build(
            instruction="x", validate=lambda sql: type("R", (), {"ok": False, "issues": ["not read-only"]})())


def test_builder_self_corrects_using_db_errors():
    # 1st draft references an invented column; the DB error is fed back and the
    # 2nd draft (good) validates → build succeeds with the corrected recipe.
    bad = {**_PAYLOAD, "charts": [{**_PAYLOAD["charts"][0],
           "sources": [{"id": "m", "sql": "SELECT region, sum(退款率数值) AS amt FROM sales GROUP BY 1"}]}]}
    llm = _SeqLLM([bad, _PAYLOAD])

    def validate(sql):
        bad_col = "退款率数值" in sql
        return {"ok": not bad_col, "issues": ["no such column: 退款率数值"] if bad_col else []}

    app = DashboardBuilderAgent(llm).build(instruction="x", validate=validate)
    assert llm.calls == 2                                            # one repair round
    assert "退款率数值" not in app.charts[0].sources[0].sql           # corrected recipe kept
    repair_msgs = [m.content for m in llm.seen if "FAILED when run against the database" in (m.content or "")]
    assert repair_msgs and "no such column: 退款率数值" in repair_msgs[0]   # error fed back
