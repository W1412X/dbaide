import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_chart_block_builds_qt_chart(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:1",
        "chart_type": "horizontal_bar",
        "title": "Factory power",
        "categories": ["A", "B"],
        "series": [{"name": "kW", "values": [10.0, 20.0]}],
        "row_count": 2,
    }
    block = ChartBlock(spec)
    assert block.layout().count() >= 2


def test_chart_block_builds_combo_dual_axis_chart(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:2",
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
    block = ChartBlock(spec)
    assert block.layout().count() >= 3


def test_chart_block_builds_stacked_area_chart(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:3",
        "chart_type": "stacked_area",
        "title": "渠道构成",
        "categories": ["Mon", "Tue", "Wed"],
        "series": [
            {"name": "自然流量", "values": [10, 12, 13], "type": "area"},
            {"name": "广告流量", "values": [4, 6, 8], "type": "area"},
        ],
        "row_count": 3,
    }
    block = ChartBlock(spec)
    assert block.layout().count() >= 3


def test_chart_block_tolerates_bad_or_short_values(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from PyQt6.QtWidgets import QLabel
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:4",
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
    block = ChartBlock(spec)
    labels = [w.text() for w in block.findChildren(QLabel)]
    assert not any("could not convert" in text for text in labels)


def test_chart_block_builds_all_right_axis_combo(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from PyQt6.QtCharts import QValueAxis
    from dbaide.desktop.components.chart_block import ChartBlock, build_chart_widget

    spec = {
        "chart_id": "chart:5",
        "chart_type": "combo",
        "title": "右轴组合",
        "categories": ["A", "B"],
        "series": [
            {"name": "成本", "values": [10, 12], "type": "bar", "axis": "right"},
            {"name": "预算", "values": [8, 9], "type": "line", "axis": "right"},
        ],
        "axes": {"right": {"label": "金额"}},
        "row_count": 2,
    }
    block = ChartBlock(spec)
    assert block.layout().count() >= 3

    widget = build_chart_widget(spec)
    chart = widget._view.chart()
    value_axes = [ax for ax in chart.axes() if isinstance(ax, QValueAxis)]
    assert len(value_axes) == 1
    assert "金额" in value_axes[0].titleText()


def test_chart_block_combo_splits_bars_by_axis(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from PyQt6.QtCharts import QBarSeries, QValueAxis
    from dbaide.desktop.components.chart_block import build_chart_widget

    spec = {
        "chart_id": "chart:7",
        "chart_type": "combo",
        "title": "左右柱",
        "categories": ["A", "B"],
        "series": [
            {"name": "销量", "values": [10, 12], "type": "bar", "axis": "left"},
            {"name": "预算", "values": [80, 90], "type": "bar", "axis": "right"},
        ],
        "axes": {
            "left": {"label": "销量"},
            "right": {"label": "预算"},
        },
        "row_count": 2,
    }
    widget = build_chart_widget(spec)
    chart = widget._view.chart()
    bar_series = [s for s in chart.series() if isinstance(s, QBarSeries)]
    value_axes = [ax for ax in chart.axes() if isinstance(ax, QValueAxis)]
    assert len(bar_series) == 2
    assert len(value_axes) == 2


def test_chart_block_scatter_non_numeric_x_falls_back_to_order(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:6",
        "chart_type": "scatter",
        "title": "散点边界",
        "categories": ["A", "B", "C"],
        "series": [{"name": "转化率", "values": [0.1, 0.2, 0.15]}],
        "row_count": 3,
    }
    block = ChartBlock(spec)
    assert block.layout().count() >= 3
