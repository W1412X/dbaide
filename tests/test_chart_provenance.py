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


def _orch(llm, *, sql: str, rows: list[dict]):
    return types.SimpleNamespace(
        llm=llm,
        run_state=types.SimpleNamespace(
            question="各工厂功率", memory=None,
            query_result=types.SimpleNamespace(sql=sql, rows=rows, columns=list(rows[0].keys()) if rows else []),
            charts=[], trace_node="node",
        ),
        progress=lambda *a, **k: None,
    )


_PLAN = {"chart_type": "horizontal_bar", "title": "功率对比",
         "category_field": "factory", "value_fields": ["power"], "sort_by": "value_desc", "limit": 20}


def test_render_chart_from_query_records_its_sql():
    sql = "SELECT factory, power FROM plants ORDER BY power DESC"
    rows = [{"factory": "A", "power": 4540.1}, {"factory": "B", "power": 4406.0}]
    orch = _orch(_ChartMockLLM(_PLAN), sql=sql, rows=rows)
    reg = _Registry()
    register(reg, orch)
    # no inline `data` → rows come from the current query result
    result = reg.handler({"intent": "对比"}, None)
    assert result.ok, getattr(result, "error", None)

    payload = orch.run_state.charts[0]
    assert payload["chart_type"] == "horizontal_bar"
    assert isinstance(payload.get("chart_plan"), dict)
    assert payload["chart_plan"]["category_field"] == "factory"
    assert payload.get("source_sql") == sql   # 1 chart ↔ the 1 SQL that produced it

    # the plan round-trips into a fresh spec (the refresh path)
    from dbaide.agent.chart_agent import ChartAgent, chart_plan_from_dict
    from dbaide.charts.spec import chart_spec_to_dict
    plan = chart_plan_from_dict(payload["chart_plan"])
    spec = ChartAgent().build_spec(plan, chart_id="x", rows=[{"factory": "C", "power": 1.0}])
    assert chart_spec_to_dict(spec)["chart_type"] == "horizontal_bar"


def test_render_chart_from_inline_data_has_no_source_sql():
    # inline/computed data has no re-runnable query — must NOT borrow the last SQL,
    # or a refresh would redraw the wrong data. The pinned tile stays a static snapshot.
    sql = "SELECT something ELSE entirely FROM other_table"
    orch = _orch(_ChartMockLLM(_PLAN), sql=sql, rows=[{"x": 1}])
    reg = _Registry()
    register(reg, orch)
    result = reg.handler({"data": [{"factory": "A", "power": 9}, {"factory": "B", "power": 4}],
                          "intent": "对比"}, None)
    assert result.ok, getattr(result, "error", None)
    payload = orch.run_state.charts[0]
    assert "source_sql" not in payload   # no SQL borrowed for inline data
    assert isinstance(payload.get("chart_plan"), dict)   # still has a plan (chart type known)
