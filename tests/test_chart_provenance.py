"""render_chart records chart_plan + source_sql on the chart payload.

This provenance is what makes a pinned chart a *re-runnable* dashboard tile, so
it must survive on the payload the agent stores. Drives the real tool handler.
"""

from __future__ import annotations

import types

from dbaide.agent.toolkit.chart_tools import register
from dbaide.llm import LLMClient, LLMMessage


class _ChartMockLLM(LLMClient):
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        return dict(self.payload)


class _Registry:
    def __init__(self) -> None:
        self.handler = None

    def register(self, _spec, fn) -> None:
        self.handler = fn


class _Orchestrator:
    def __init__(self, llm, sql: str) -> None:
        self.llm = llm
        self.run_state = types.SimpleNamespace(
            question="各工厂功率",
            memory=None,
            query_result=types.SimpleNamespace(sql=sql, rows=[], columns=[]),
            charts=[],
            trace_node="node",
        )

    def progress(self, *_args, **_kwargs) -> None:
        pass


def test_render_chart_attaches_plan_and_source_sql():
    sql = "SELECT factory, power FROM plants ORDER BY power DESC"
    llm = _ChartMockLLM({
        "chart_type": "horizontal_bar", "title": "功率对比",
        "category_field": "factory", "value_fields": ["power"],
        "sort_by": "value_desc", "limit": 20,
    })
    orch = _Orchestrator(llm, sql)
    reg = _Registry()
    register(reg, orch)

    rows = [{"factory": "A", "power": 4540.1}, {"factory": "B", "power": 4406.0}]
    result = reg.handler({"data": rows, "intent": "对比"}, None)
    assert result.ok, getattr(result, "error", None)

    assert len(orch.run_state.charts) == 1
    payload = orch.run_state.charts[0]
    assert payload["chart_type"] == "horizontal_bar"
    # the two provenance keys that make this chart a re-runnable tile
    assert isinstance(payload.get("chart_plan"), dict)
    assert payload["chart_plan"]["category_field"] == "factory"
    assert payload["chart_plan"]["value_fields"] == ["power"]
    assert payload.get("source_sql") == sql

    # and the plan round-trips into a fresh spec (the refresh path)
    from dbaide.agent.chart_agent import ChartAgent, chart_plan_from_dict
    from dbaide.charts.spec import chart_spec_to_dict
    plan = chart_plan_from_dict(payload["chart_plan"])
    spec = ChartAgent().build_spec(plan, chart_id="x", rows=[{"factory": "C", "power": 1.0}])
    assert chart_spec_to_dict(spec)["chart_type"] == "horizontal_bar"
