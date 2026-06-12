import pytest

from dbaide.agent.chart_agent import ChartAgent
from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.charts.spec import chart_spec_to_dict
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient


class _ChartMockLLM(LLMClient):
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        return dict(self.payload)


def test_chart_agent_uses_llm_chart_type():
    rows = [
        {"factory": "快讯网络有限公司制造基地", "power": 4540.1},
        {"factory": "毕博诚科技有限公司制造基地", "power": 4406.0},
    ]
    llm = _ChartMockLLM({
        "chart_type": "horizontal_bar",
        "title": "功率对比",
        "category_field": "factory",
        "value_fields": ["power"],
        "series_names": ["功率 (kW)"],
        "x_label": "功率 (kW)",
        "y_label": "",
        "sort_by": "value_desc",
        "limit": 20,
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:1",
        question="各工厂功率",
        intent="对比",
        columns=["factory", "power"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["chart_type"] == "horizontal_bar"
    assert payload["categories"][0].startswith("快讯")


def test_chart_agent_requires_llm():
    rows = [{"factory": "A", "power": 1}]
    with pytest.raises(ModelRequiredError):
        ChartAgent(NullLLMClient()).plan(
            question="q",
            intent="",
            columns=["factory", "power"],
            rows=rows,
        )


@pytest.mark.parametrize(
    "payload,match",
    [
        (
            {"chart_type": "bar", "category_field": "", "value_fields": ["power"]},
            "category_field",
        ),
        (
            {"chart_type": "bar", "category_field": "factory", "value_fields": []},
            "value_fields",
        ),
        (
            {"chart_type": "bar", "category_field": "missing", "value_fields": ["power"]},
            "category_field",
        ),
        (
            {"chart_type": "bar", "category_field": "factory", "value_fields": ["nope"]},
            "value_fields not in columns",
        ),
    ],
)
def test_chart_agent_rejects_incomplete_or_invalid_fields(payload, match):
    rows = [{"factory": "A", "power": 1}]
    with pytest.raises(ValueError, match=match):
        ChartAgent(_ChartMockLLM(payload)).plan(
            question="q",
            intent="",
            columns=["factory", "power"],
            rows=rows,
        )
