import pytest
import sys
import types

from dbaide.charts.echarts import chart_spec_to_echarts_option, render_echarts_html


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_echarts_option_bar_keeps_zero_baseline_and_categories():
    spec = {
        "chart_id": "chart:bar",
        "chart_type": "bar",
        "title": "Losses",
        "categories": ["Q1", "Q2", "Q3"],
        "series": [{"name": "净利润", "values": [-10.0, -25.0, -5.0], "type": "bar"}],
        "row_count": 3,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["xAxis"]["data"] == ["Q1", "Q2", "Q3"]
    assert option["yAxis"][0]["scale"] is False
    assert option["series"][0]["type"] == "bar"
    assert option["series"][0]["data"] == [-10.0, -25.0, -5.0]


def test_echarts_option_horizontal_bar_uses_value_x_axis():
    spec = {
        "chart_id": "chart:hbar",
        "chart_type": "horizontal_bar",
        "title": "Factory power",
        "categories": ["A", "B"],
        "series": [{"name": "kW", "values": [10.0, 20.0]}],
        "row_count": 2,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["xAxis"]["type"] == "value"
    assert option["yAxis"]["type"] == "category"
    assert option["yAxis"]["inverse"] is True
    assert option["series"][0]["type"] == "bar"


def test_echarts_option_combo_dual_axis_splits_right_axis():
    spec = {
        "chart_id": "chart:combo",
        "chart_type": "combo",
        "title": "销量与广告投入",
        "categories": ["2026-06-01", "2026-06-02"],
        "series": [
            {"name": "销量", "values": [120, 150], "type": "bar", "axis": "left", "unit": "单"},
            {"name": "广告投入", "values": [3500, 4200], "type": "line", "axis": "right", "unit": "元"},
        ],
        "axes": {
            "left": {"label": "销量", "format": "number"},
            "right": {"label": "广告投入", "format": "currency"},
        },
        "row_count": 2,
    }

    option = chart_spec_to_echarts_option(spec)

    assert len(option["yAxis"]) == 2
    assert option["yAxis"][1]["name"] == "广告投入"
    assert option["yAxis"][1]["_valueFormat"] == "currency"
    assert option["yAxis"][1]["_compactValues"] is True
    assert [s["yAxisIndex"] for s in option["series"]] == [0, 1]
    assert [s["type"] for s in option["series"]] == ["bar", "line"]


def test_echarts_option_combo_area_series_keeps_area_style():
    spec = {
        "chart_id": "chart:combo-area",
        "chart_type": "combo",
        "title": "Volume",
        "categories": ["A", "B"],
        "series": [
            {"name": "total", "values": [10, 20], "type": "bar"},
            {"name": "running", "values": [8, 18], "type": "area"},
        ],
        "row_count": 2,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][1]["type"] == "line"
    assert "areaStyle" in option["series"][1]


def test_echarts_option_line_honors_straight_segments_and_step_mode():
    spec = {
        "chart_id": "chart:line-straight",
        "chart_type": "line",
        "title": "Trend",
        "categories": ["A", "B", "C"],
        "series": [{"name": "value", "values": [1, 3, 2]}],
        "options": {"smooth": False, "step": "middle", "show_symbols": True},
        "row_count": 3,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][0]["smooth"] is False
    assert option["series"][0]["step"] == "middle"
    assert option["series"][0]["showSymbol"] is True


def test_echarts_option_stacked_area_sets_stack_and_area_style():
    spec = {
        "chart_id": "chart:area",
        "chart_type": "stacked_area",
        "title": "渠道构成",
        "categories": ["Mon", "Tue", "Wed"],
        "series": [
            {"name": "自然流量", "values": [10, 12, 13], "type": "area"},
            {"name": "广告流量", "values": [4, 6, 8], "type": "area"},
        ],
        "row_count": 3,
    }

    option = chart_spec_to_echarts_option(spec)

    assert all(s["type"] == "line" for s in option["series"])
    assert all(s["stack"] == "total" for s in option["series"])
    assert all("areaStyle" in s for s in option["series"])


def test_echarts_option_scatter_numeric_x_keeps_actual_values():
    spec = {
        "chart_id": "chart:scatter",
        "chart_type": "scatter",
        "title": "spread",
        "categories": ["100", "2500", "5000"],
        "series": [{"name": "y", "values": [1.0, 2.0, 3.0]}],
        "row_count": 3,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["xAxis"]["type"] == "value"
    assert option["series"][0]["data"] == [[100.0, 1.0], [2500.0, 2.0], [5000.0, 3.0]]


def test_echarts_option_bubble_uses_point_payload_and_per_item_symbol_sizes():
    spec = {
        "chart_id": "chart:bubble",
        "chart_type": "bubble",
        "title": "Bubble",
        "series": [{"name": "value", "values": [1, 2]}],
        "data": {
            "points": [
                {"name": "A", "x": 10, "y": 3, "size": 25},
                {"name": "B", "x": 12, "y": 4, "size": 9},
            ],
        },
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][0]["type"] == "scatter"
    assert option["series"][0]["data"][0]["value"] == [10.0, 3.0, 25.0]
    assert option["series"][0]["data"][0]["symbolSize"] > option["series"][0]["data"][1]["symbolSize"]


def test_echarts_option_tolerates_bad_or_short_values():
    spec = {
        "chart_id": "chart:bad",
        "chart_type": "combo",
        "title": "边界值",
        "categories": ["A", "B", "C"],
        "series": [
            {"name": "销量", "values": [10, "bad"], "type": "bar", "axis": "left"},
            {"name": "投入", "values": [float("nan"), 2, 3, 4], "type": "line", "axis": "right"},
        ],
        "axes": {"right": {"label": "投入"}},
        "row_count": 3,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][0]["data"] == [10.0, 0.0, 0.0]
    assert option["series"][1]["data"] == [0.0, 2.0, 3.0]


def test_render_echarts_html_contains_option_and_loader():
    spec = {
        "chart_id": "chart:html",
        "chart_type": "line",
        "title": "趋势",
        "categories": ["A", "B"],
        "series": [{"name": "value", "values": [1, 2]}],
        "row_count": 2,
    }

    html = render_echarts_html(spec, echarts_src="qrc:/echarts.min.js")

    assert "echarts.init" in html
    assert "qrc:/echarts.min.js" in html
    assert '"type":"line"' in html
    assert "function (value)" not in html


def test_echarts_option_heatmap_uses_visual_map_and_points():
    spec = {
        "chart_id": "chart:heatmap",
        "chart_type": "heatmap",
        "title": "Heatmap",
        "data": {
            "x_categories": ["Mon", "Tue"],
            "y_categories": ["App", "Web"],
            "points": [[0, 0, 10], [1, 0, 12], [0, 1, 8]],
        },
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][0]["type"] == "heatmap"
    assert option["visualMap"]["max"] == 12.0
    assert option["series"][0]["data"][1] == [1, 0, 12.0]


def test_echarts_option_radar_uses_special_payload():
    spec = {
        "chart_id": "chart:radar",
        "chart_type": "radar",
        "title": "Radar",
        "options": {"radar_shape": "circle", "legend_position": "top"},
        "data": {
            "indicators": [{"name": "A", "max": 10}, {"name": "B", "max": 20}],
            "radar_series": [{"name": "Alpha", "value": [8, 12]}, {"name": "Beta", "value": [6, 16]}],
        },
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["radar"]["shape"] == "circle"
    assert option["legend"]["top"] == 4
    assert option["series"][0]["type"] == "radar"


def test_echarts_option_scatter_accepts_points_without_series():
    spec = {
        "chart_id": "chart:scatter",
        "chart_type": "scatter",
        "title": "Scatter",
        "series": [],
        "data": {"points": [{"name": "A", "x": 1.0, "y": 2.0}]},
    }
    option = chart_spec_to_echarts_option(spec)
    assert option["series"][0]["type"] == "scatter"
    assert option["series"][0]["data"][0]["value"] == [1.0, 2.0]


def test_echarts_option_gauge_uses_range_options():
    spec = {
        "chart_id": "chart:gauge",
        "chart_type": "gauge",
        "title": "Gauge",
        "options": {"gauge_min": 0, "gauge_max": 200, "gauge_target": 150},
        "data": {"value": 132, "name": "Completion"},
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["series"][0]["type"] == "gauge"
    assert option["series"][0]["min"] == 0
    assert option["series"][0]["max"] == 200
    assert option["series"][0]["data"][0]["value"] == 132.0
    assert option["series"][0]["axisLine"]["lineStyle"]["color"][0][0] == pytest.approx(0.66, abs=0.01)
    assert "150" in option["series"][0]["detail"]["formatter"]


def test_echarts_option_sankey_and_tree_types_render():
    sankey = {
        "chart_id": "chart:sankey",
        "chart_type": "sankey",
        "title": "Flow",
        "options": {"node_align": "left"},
        "data": {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "links": [{"source": "A", "target": "B", "value": 10}],
        },
    }
    sankey_option = chart_spec_to_echarts_option(sankey)
    assert sankey_option["series"][0]["type"] == "sankey"
    assert sankey_option["series"][0]["nodeAlign"] == "left"

    treemap = {
        "chart_id": "chart:tree",
        "chart_type": "treemap",
        "title": "Tree",
        "data": {"tree": [{"name": "A", "value": 10}, {"name": "B", "value": 5}]},
    }
    treemap_option = chart_spec_to_echarts_option(treemap)
    assert treemap_option["series"][0]["type"] == "treemap"

    # Node-link tree: single supplied root is used directly; multiple roots are hung
    # under a synthetic root so the series stays a valid tree.
    tree = {
        "chart_id": "chart:nodetree",
        "chart_type": "tree",
        "title": "orders 依赖树",
        "data": {"tree": [{"name": "orders", "children": [
            {"name": "payments", "children": [{"name": "ledger_entries"}]},
            {"name": "refunds"},
        ]}]},
    }
    tree_option = chart_spec_to_echarts_option(tree)
    series = tree_option["series"][0]
    assert series["type"] == "tree"
    assert series["data"][0]["name"] == "orders"
    assert {c["name"] for c in series["data"][0]["children"]} == {"payments", "refunds"}

    multi_root = chart_spec_to_echarts_option({
        "chart_id": "chart:mr", "chart_type": "tree", "title": "lineage",
        "data": {"tree": [{"name": "a"}, {"name": "b"}]},
    })
    assert multi_root["series"][0]["data"][0]["name"] == "lineage"
    assert len(multi_root["series"][0]["data"][0]["children"]) == 2


def test_echarts_option_candlestick_boxplot_and_waterfall_render():
    candlestick = {
        "chart_id": "chart:k",
        "chart_type": "candlestick",
        "title": "K",
        "categories": ["2026-06-01", "2026-06-02"],
        "data": {"ohlc": [[10, 12, 9, 14], [12, 11, 10, 13]]},
    }
    candle_option = chart_spec_to_echarts_option(candlestick)
    assert candle_option["series"][0]["type"] == "candlestick"

    boxplot = {
        "chart_id": "chart:box",
        "chart_type": "boxplot",
        "title": "Box",
        "categories": ["A", "B"],
        "data": {"boxes": [[1, 2, 3, 4, 5], [2, 3, 4, 5, 6]], "outliers": []},
    }
    box_option = chart_spec_to_echarts_option(boxplot)
    assert box_option["series"][0]["type"] == "boxplot"

    waterfall = {
        "chart_id": "chart:waterfall",
        "chart_type": "waterfall",
        "title": "Waterfall",
        "categories": ["Start", "Gain", "Loss"],
        "series": [{"name": "PnL", "values": [100, 50, -20]}],
    }
    waterfall_option = chart_spec_to_echarts_option(waterfall)
    assert len(waterfall_option["series"]) == 2
    assert waterfall_option["series"][1]["type"] == "bar"


def test_chart_block_empty_series_shows_no_data(qapp):
    from PyQt6.QtWidgets import QLabel

    from dbaide.desktop.components.chart_block import build_chart_widget

    for spec in (
        {"chart_type": "bar", "categories": ["A", "B"], "series": [], "row_count": 0},
        {"chart_type": "pie", "categories": [], "series": [{"name": "n", "values": []}]},
        {"chart_type": "line", "categories": ["A"], "series": [{"name": "n", "values": []}]},
        {"chart_type": "heatmap", "data": {"x_categories": ["A"], "y_categories": ["B"], "points": []}},
    ):
        widget = build_chart_widget(spec)
        assert isinstance(widget, QLabel)
        assert widget.text()


def test_chart_block_wraps_webengine_chart(qapp, monkeypatch):
    from PyQt6.QtWidgets import QWidget

    class _FakeWebEngineView(QWidget):
        def setHtml(self, html, base_url):  # noqa: N802 - Qt API shape
            self.html = html
            self.base_url = base_url

    fake_module = types.ModuleType("PyQt6.QtWebEngineWidgets")
    fake_module.QWebEngineView = _FakeWebEngineView
    monkeypatch.setitem(sys.modules, "PyQt6.QtWebEngineWidgets", fake_module)
    monkeypatch.setenv("DBAIDE_ECHARTS_SRC", "qrc:/vendor/echarts.min.js")
    from dbaide.desktop.components.chart_block import ChartBlock, build_chart_widget

    spec = {
        "chart_id": "chart:web",
        "chart_type": "bar",
        "title": "Sales",
        "categories": ["A", "B"],
        "series": [{"name": "value", "values": [1, 2]}],
        "row_count": 2,
    }

    widget = build_chart_widget(spec)
    block = ChartBlock(spec)
    assert widget.minimumWidth() >= 280
    assert "echarts.init" in widget.html
    assert "qrc:/vendor/echarts.min.js" in widget.html
    assert block.layout().count() >= 3


def test_echarts_option_dense_dates_rotate_and_compact_labels():
    dates = [f"2026-06-{day:02d}" for day in range(1, 11)]
    spec = {
        "chart_id": "chart:line-dates",
        "chart_type": "line",
        "title": "Daily",
        "categories": dates,
        "series": [
            {"name": "App", "values": [120000.0 + i * 1000 for i in range(10)]},
            {"name": "Web", "values": [450000.0 - i * 5000 for i in range(10)]},
            {"name": "Kol", "values": [7600.0 + i * 10 for i in range(10)]},
        ],
        "y_label": "销售额",
        "row_count": 10,
    }

    option = chart_spec_to_echarts_option(spec)

    assert option["xAxis"]["axisLabel"]["rotate"] != 0
    assert option["xAxis"]["data"][0] == "06-01"
    assert len(option["yAxis"]) == 2
    assert option["grid"]["containLabel"] is True
    assert option["grid"]["top"] >= 40
    assert "dataZoom" not in option

    interactive = chart_spec_to_echarts_option(spec, theme={"chartInteractive": True})
    assert "dataZoom" in interactive
    assert {s["yAxisIndex"] for s in option["series"]} == {0, 1}
    assert sum(1 for s in option["series"] if s["yAxisIndex"] == 1) == 1
    assert option["series"][2]["yAxisIndex"] == 1
