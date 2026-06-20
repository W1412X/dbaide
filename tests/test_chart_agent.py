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
    assert payload["series"][0]["axis"] == "left"
    assert payload["series"][1]["axis"] == "right"
    assert payload["series"][0]["unit"] == "单"


def test_chart_agent_line_options_and_special_data_materialize():
    rows = [
        {"day": "2026-06-01", "sales": 120, "spend": 35.5},
        {"day": "2026-06-02", "sales": 150, "spend": 42.0},
    ]
    llm = _ChartMockLLM({
        "chart_type": "line",
        "title": "趋势",
        "category_field": "day",
        "value_fields": ["sales"],
        "series_names": ["销售额"],
        "options": {"smooth": False, "step": "start", "show_symbols": True},
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:1",
        question="q",
        intent="趋势",
        columns=["day", "sales", "spend"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["options"]["smooth"] is False
    assert payload["options"]["step"] == "start"


def test_chart_agent_materializes_heatmap():
    rows = [
        {"weekday": "Mon", "channel": "App", "sales": 10},
        {"weekday": "Tue", "channel": "App", "sales": 12},
        {"weekday": "Mon", "channel": "Web", "sales": 8},
        {"weekday": "Mon", "channel": "App", "sales": 2},
    ]
    llm = _ChartMockLLM({
        "chart_type": "heatmap",
        "title": "热力图",
        "x_field": "weekday",
        "y_field": "channel",
        "value_fields": ["sales"],
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:2",
        question="q",
        intent="热力图",
        columns=["weekday", "channel", "sales"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["chart_type"] == "heatmap"
    assert payload["data"]["x_categories"] == ["Mon", "Tue"]
    assert len(payload["data"]["points"]) == 3
    mon_app = next(p for p in payload["data"]["points"] if p[0] == 0 and p[1] == 0)
    assert mon_app[2] == 12.0


def test_chart_agent_heatmap_requires_value_fields():
    rows = [{"weekday": "Mon", "channel": "App", "sales": 10}]
    with pytest.raises(ValueError, match="value_fields"):
        ChartAgent(_ChartMockLLM({
            "chart_type": "heatmap",
            "title": "热力图",
            "x_field": "weekday",
            "y_field": "channel",
            "value_fields": [],
        })).plan(
            question="q",
            intent="热力图",
            columns=["weekday", "channel", "sales"],
            rows=rows,
        )


def test_chart_agent_gauge_uses_category_label():
    rows = [{"metric": "Completion", "score": 88.0}]
    llm = _ChartMockLLM({
        "chart_type": "gauge",
        "title": "KPI",
        "category_field": "metric",
        "value_fields": ["score"],
        "series_names": [],
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:g",
        question="q",
        intent="KPI",
        columns=["metric", "score"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["data"]["name"] == "Completion"
    assert payload["data"]["value"] == 88.0


def test_chart_agent_funnel_sort_order_follows_sort_by():
    rows = [
        {"stage": "Visit", "count": 100},
        {"stage": "Signup", "count": 40},
        {"stage": "Paid", "count": 10},
    ]
    llm = _ChartMockLLM({
        "chart_type": "funnel",
        "title": "Funnel",
        "category_field": "stage",
        "value_fields": ["count"],
        "sort_by": "category_asc",
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:f",
        question="q",
        intent="funnel",
        columns=["stage", "count"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert payload["options"]["sort_order"] == "none"
    assert payload["categories"] == ["Paid", "Signup", "Visit"]


def test_chart_agent_treemap_aggregates_duplicate_paths():
    rows = [
        {"region": "East", "product": "A", "sales": 10},
        {"region": "East", "product": "A", "sales": 5},
    ]
    llm = _ChartMockLLM({
        "chart_type": "treemap",
        "title": "Sales",
        "path_fields": ["region", "product"],
        "value_fields": ["sales"],
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:t",
        question="q",
        intent="treemap",
        columns=["region", "product", "sales"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    leaf = payload["data"]["tree"][0]["children"][0]
    assert leaf["name"] == "A"
    assert leaf["value"] == 15.0


def test_chart_agent_materializes_sankey_aggregates_duplicate_links():
    rows = [
        {"source": "A", "target": "B", "amount": 10},
        {"source": "A", "target": "B", "amount": 5},
        {"source": "B", "target": "C", "amount": 3},
    ]
    llm = _ChartMockLLM({
        "chart_type": "sankey",
        "title": "Flow",
        "source_field": "source",
        "target_field": "target",
        "value_fields": ["amount"],
    })
    spec = ChartAgent(llm).render(
        chart_id="chart:s",
        question="q",
        intent="flow",
        columns=["source", "target", "amount"],
        rows=rows,
    )
    payload = chart_spec_to_dict(spec)
    assert len(payload["data"]["links"]) == 2
    ab = next(link for link in payload["data"]["links"] if link["source"] == "A")
    assert ab["value"] == 15.0


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
