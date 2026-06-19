"""Chart spec models and ECharts rendering helpers (no Qt dependency)."""

from dbaide.charts.echarts import chart_spec_to_echarts_option, render_echarts_html
from dbaide.charts.spec import ChartSpec, chart_spec_from_dict, chart_spec_to_dict

__all__ = [
    "ChartSpec",
    "chart_spec_from_dict",
    "chart_spec_to_dict",
    "chart_spec_to_echarts_option",
    "render_echarts_html",
]
