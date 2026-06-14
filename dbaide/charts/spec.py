"""Serializable chart specification consumed by the Qt Charts UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHART_TYPES = frozenset({
    "bar", "horizontal_bar", "line", "area", "pie", "donut", "stacked_bar", "scatter",
    "combo", "grouped_bar", "stacked_area", "multi_axis_line",
})


@dataclass(slots=True)
class ChartSpec:
    chart_id: str
    chart_type: str
    title: str
    categories: list[str] = field(default_factory=list)
    series: list[dict[str, Any]] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    row_count: int = 0
    axes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def validate(self) -> None:
        if self.chart_type not in CHART_TYPES:
            raise ValueError(f"unsupported chart_type: {self.chart_type!r}")
        if not self.series:
            raise ValueError("chart requires at least one series")
        for item in self.series:
            values = item.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError("each series requires non-empty values")
        if self.chart_type not in ("pie", "donut", "scatter") and not self.categories:
            raise ValueError("chart requires categories")
        for item in self.series:
            series_type = str(item.get("type") or "").strip()
            if series_type and series_type not in {"bar", "line", "area"}:
                raise ValueError(f"unsupported series type: {series_type!r}")
            axis = str(item.get("axis") or "").strip()
            if axis and axis not in {"left", "right"}:
                raise ValueError(f"unsupported series axis: {axis!r}")


def chart_spec_to_dict(spec: ChartSpec) -> dict[str, Any]:
    return {
        "chart_id": spec.chart_id,
        "chart_type": spec.chart_type,
        "title": spec.title,
        "categories": list(spec.categories),
        "series": [dict(s) for s in spec.series],
        "x_label": spec.x_label,
        "y_label": spec.y_label,
        "row_count": spec.row_count,
        "axes": {str(k): dict(v) for k, v in (spec.axes or {}).items() if isinstance(v, dict)},
    }


def chart_spec_from_dict(data: dict[str, Any]) -> ChartSpec:
    spec = ChartSpec(
        chart_id=str(data.get("chart_id") or ""),
        chart_type=str(data.get("chart_type") or "bar"),
        title=str(data.get("title") or ""),
        categories=[str(x) for x in (data.get("categories") or [])],
        series=[dict(x) for x in (data.get("series") or []) if isinstance(x, dict)],
        x_label=str(data.get("x_label") or ""),
        y_label=str(data.get("y_label") or ""),
        row_count=int(data.get("row_count") or 0),
        axes={
            str(k): dict(v)
            for k, v in (data.get("axes") or {}).items()
            if isinstance(v, dict)
        } if isinstance(data.get("axes"), dict) else {},
    )
    spec.validate()
    return spec
