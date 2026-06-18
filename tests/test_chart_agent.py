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


def test_chart_agent_clamps_overlong_labels_deterministically():
    """The model no longer counts characters — the app clamps long labels in code."""
    rows = [{"d": "2026-06-01", "v": 10.0}]
    long_title = "A very long descriptive chart title that exceeds the display budget"
    llm = _ChartMockLLM({
        "chart_type": "bar",
        "title": long_title,
        "category_field": "d",
        "value_fields": ["v"],
        "series_names": ["A rather long series legend name"],
        "x_label": "An overly long x axis label here",
        "y_label": "Another overly long y axis label",
        "axes": {"left": {"label": "An overly long left axis label", "format": "number"}},
        "sort_by": "category_asc",
        "limit": 10,
    })
    spec = ChartAgent(llm).render(chart_id="chart:1", question="q", intent="i",
                                  columns=["d", "v"], rows=rows)
    payload = chart_spec_to_dict(spec)
    assert len(payload["title"]) <= 40 and payload["title"].endswith("…")
    assert len(payload["x_label"]) <= 18 and payload["x_label"].endswith("…")
    assert len(payload["y_label"]) <= 18
    assert len(payload["series"][0]["name"]) <= 16 and payload["series"][0]["name"].endswith("…")
    assert len(payload["axes"]["left"]["label"]) <= 18


def test_chart_agent_materializes_combo_dual_axis_metadata():
    rows = [
        {"day": "2026-06-01", "orders": 120, "ad_spend": 3500.0},
        {"day": "2026-06-02", "orders": 150, "ad_spend": 4200.0},
    ]
    llm = _ChartMockLLM({
        "chart_type": "combo",
        "title": "销量与广告投入",
        "category_field": "day",
        "value_fields": ["orders", "ad_spend"],
        "series_names": ["订单量", "广告投入"],
        "series_types": ["bar", "line"],
        "series_axes": ["left", "right"],
        "units": ["单", "元"],
        "axes": {
            "left": {"label": "订单量", "format": "number"},
            "right": {"label": "广告投入", "format": "currency"},
        },
        "x_label": "日期",
        "y_label": "订单量",
        "sort_by": "category_asc",
        "limit": 20,
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:1",
        question="销量和广告投入趋势",
        intent="对齐展示销量与广告投入",
        columns=["day", "orders", "ad_spend"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["chart_type"] == "combo"
    assert payload["series"][0]["type"] == "bar"
    assert payload["series"][1]["axis"] == "right"
    assert payload["axes"]["right"]["label"] == "广告投入"


def test_chart_agent_tolerates_scalar_optional_fields():
    rows = [{"day": "2026-06-01", "orders": 120, "spend": 35.5}]
    llm = _ChartMockLLM({
        "chart_type": "combo",
        "title": "趋势",
        "category_field": "day",
        "value_fields": ["orders", "spend"],
        "series_names": ["订单量", "广告投入"],
        "series_types": "line",
        "series_axes": "right",
        "units": "单",
        "limit": "not-a-number",
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:1",
        question="q",
        intent="趋势",
        columns=["day", "orders", "spend"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["series"][0]["type"] == "line"
    assert payload["series"][1]["type"] == "line"
    assert payload["series"][0]["axis"] == "right"
    assert payload["series"][1]["axis"] == "right"
    assert payload["series"][0]["unit"] == "单"


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
