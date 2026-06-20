"""Chart layout helpers shared by desktop blocks and answer rendering."""

from __future__ import annotations

from typing import Any

from dbaide.charts.labels import category_axis_layout


def estimate_chart_height(
    chart_type: str,
    category_count: int,
    categories: list[str] | None = None,
) -> int:
    """Return a fixed pixel height for a chart canvas inside a conversation block."""
    cats = list(categories or [])
    _, angle, bottom_extra = category_axis_layout(cats) if cats else ("", 0, 0)
    if chart_type == "horizontal_bar":
        return min(560, max(240, 52 * max(category_count, 1) + 80))
    if chart_type in {"pie", "donut"}:
        return 360
    if chart_type == "gauge":
        return 320
    if chart_type in {"radar", "sunburst"}:
        return 420
    if chart_type in {"heatmap", "treemap", "sankey"}:
        return 440
    if chart_type in {"funnel"}:
        return 400
    if chart_type in {"candlestick", "boxplot", "waterfall"}:
        return 380
    base = 280 + bottom_extra
    if angle:
        base += 16
    if category_count > 10:
        base += 24
    if category_count > 1:
        base += min(120, 18 * category_count)
    return min(560, max(320, base))


def estimate_chart_height_from_spec(spec_dict: dict[str, Any]) -> int:
    chart_type = str(spec_dict.get("chart_type") or "bar")
    categories = list(spec_dict.get("categories") or [])
    return estimate_chart_height(chart_type, len(categories), categories)
