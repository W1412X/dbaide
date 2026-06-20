"""Serializable chart specification consumed by the desktop chart renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHART_TYPES = frozenset({
    "bar",
    "horizontal_bar",
    "grouped_bar",
    "stacked_bar",
    "line",
    "area",
    "stacked_area",
    "multi_axis_line",
    "combo",
    "scatter",
    "bubble",
    "pie",
    "donut",
    "radar",
    "heatmap",
    "funnel",
    "gauge",
    "sankey",
    "treemap",
    "sunburst",
    "waterfall",
    "candlestick",
    "boxplot",
})

SERIES_TYPES = frozenset({"bar", "line", "area"})
SERIES_AXES = frozenset({"left", "right"})
AXIS_FORMATS = frozenset({"", "number", "currency", "percent"})
STEP_VALUES = frozenset({"", "none", "start", "middle", "end"})
LABEL_MODES = frozenset({"", "inside", "outside", "top", "center", "none"})
LEGEND_POSITIONS = frozenset({"", "top", "bottom", "left", "right"})
RADAR_SHAPES = frozenset({"", "polygon", "circle"})
NODE_ALIGNS = frozenset({"", "justify", "left", "right"})
SORT_ORDERS = frozenset({"", "ascending", "descending", "none"})


def _clamp_ratio(value: Any, *, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, ratio))


def _safe_int(value: Any, *, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class ChartOptions:
    smooth: bool | None = None
    step: str = ""
    show_symbols: bool | None = None
    stacked: bool | None = None
    show_labels: bool | None = None
    label_mode: str = ""
    area_opacity: float = 0.18
    bar_max_width: int = 42
    donut_inner_ratio: float = 0.56
    rose: bool = False
    radar_shape: str = ""
    node_align: str = ""
    legend_position: str = ""
    gauge_min: float | None = None
    gauge_max: float | None = None
    gauge_target: float | None = None
    sort_order: str = ""

    def validate(self) -> None:
        if self.step not in STEP_VALUES:
            raise ValueError(f"unsupported step option: {self.step!r}")
        if self.label_mode not in LABEL_MODES:
            raise ValueError(f"unsupported label_mode: {self.label_mode!r}")
        if self.legend_position not in LEGEND_POSITIONS:
            raise ValueError(f"unsupported legend_position: {self.legend_position!r}")
        if self.radar_shape not in RADAR_SHAPES:
            raise ValueError(f"unsupported radar_shape: {self.radar_shape!r}")
        if self.node_align not in NODE_ALIGNS:
            raise ValueError(f"unsupported node_align: {self.node_align!r}")
        if self.sort_order not in SORT_ORDERS:
            raise ValueError(f"unsupported sort_order: {self.sort_order!r}")
        self.area_opacity = _clamp_ratio(self.area_opacity, default=0.18, minimum=0.0, maximum=0.8)
        self.donut_inner_ratio = _clamp_ratio(self.donut_inner_ratio, default=0.56, minimum=0.15, maximum=0.9)
        self.bar_max_width = _safe_int(self.bar_max_width, default=42, minimum=8, maximum=72)

    def to_dict(self) -> dict[str, Any]:
        return {
            "smooth": self.smooth,
            "step": self.step,
            "show_symbols": self.show_symbols,
            "stacked": self.stacked,
            "show_labels": self.show_labels,
            "label_mode": self.label_mode,
            "area_opacity": self.area_opacity,
            "bar_max_width": self.bar_max_width,
            "donut_inner_ratio": self.donut_inner_ratio,
            "rose": self.rose,
            "radar_shape": self.radar_shape,
            "node_align": self.node_align,
            "legend_position": self.legend_position,
            "gauge_min": self.gauge_min,
            "gauge_max": self.gauge_max,
            "gauge_target": self.gauge_target,
            "sort_order": self.sort_order,
        }


def _normalize_step(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "none" if text == "false" else text


def chart_options_from_dict(data: dict[str, Any] | None) -> ChartOptions:
    data = dict(data or {})
    options = ChartOptions(
        smooth=data.get("smooth") if isinstance(data.get("smooth"), bool) else None,
        step=_normalize_step(data.get("step")),
        show_symbols=data.get("show_symbols") if isinstance(data.get("show_symbols"), bool) else None,
        stacked=data.get("stacked") if isinstance(data.get("stacked"), bool) else None,
        show_labels=data.get("show_labels") if isinstance(data.get("show_labels"), bool) else None,
        label_mode=str(data.get("label_mode") or "").strip().lower(),
        area_opacity=_clamp_ratio(data.get("area_opacity"), default=0.18, minimum=0.0, maximum=0.8),
        bar_max_width=_safe_int(data.get("bar_max_width"), default=42),
        donut_inner_ratio=_clamp_ratio(data.get("donut_inner_ratio"), default=0.56, minimum=0.15, maximum=0.9),
        rose=bool(data.get("rose") or False),
        radar_shape=str(data.get("radar_shape") or "").strip().lower(),
        node_align=str(data.get("node_align") or "").strip().lower(),
        legend_position=str(data.get("legend_position") or "").strip().lower(),
        gauge_min=_safe_float(data.get("gauge_min")),
        gauge_max=_safe_float(data.get("gauge_max")),
        gauge_target=_safe_float(data.get("gauge_target")),
        sort_order=str(data.get("sort_order") or "").strip().lower(),
    )
    options.validate()
    return options


def normalize_axis_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(raw or {})
    fmt = str(data.get("format") or "").strip().lower()
    if fmt not in AXIS_FORMATS:
        fmt = ""
    return {
        "label": str(data.get("label") or "").strip(),
        "format": fmt,
        "min": _safe_float(data.get("min")),
        "max": _safe_float(data.get("max")),
        "inverse": bool(data.get("inverse")) if isinstance(data.get("inverse"), bool) else False,
        "log": bool(data.get("log")) if isinstance(data.get("log"), bool) else False,
    }


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
    options: ChartOptions = field(default_factory=ChartOptions)
    data: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.chart_type not in CHART_TYPES:
            raise ValueError(f"unsupported chart_type: {self.chart_type!r}")
        self.options.validate()
        self.axes = {
            str(k): normalize_axis_config(v)
            for k, v in (self.axes or {}).items()
            if isinstance(v, dict) and str(k) in {"left", "right", "x", "y"}
        }
        self.data = dict(self.data or {})

        if self.chart_type in {"scatter", "bubble", "heatmap", "sankey", "treemap", "sunburst", "candlestick", "boxplot", "gauge", "radar"}:
            self._validate_special_payload()
        elif self.chart_type in {"bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line", "area", "stacked_area", "multi_axis_line", "combo", "pie", "donut", "funnel", "waterfall"}:
            self._validate_series_categories()

    def _validate_special_payload(self) -> None:
        if self.chart_type == "radar":
            self._require_data_keys("indicators", "radar_series")
        elif self.chart_type == "heatmap":
            self._require_data_keys("x_categories", "y_categories", "points")
        elif self.chart_type == "gauge":
            self._require_data_keys("value")
        elif self.chart_type == "sankey":
            self._require_data_keys("nodes", "links")
        elif self.chart_type in {"treemap", "sunburst"}:
            self._require_data_keys("tree")
        elif self.chart_type == "candlestick":
            self._require_data_keys("ohlc")
            if not self.categories:
                raise ValueError("candlestick requires categories")
        elif self.chart_type == "boxplot":
            self._require_data_keys("boxes")
            if not self.categories:
                raise ValueError("boxplot requires categories")
        elif self.chart_type in {"scatter", "bubble"}:
            points = self.data.get("points")
            if isinstance(points, list) and points:
                return
            self._validate_series_categories()

    def _validate_series_categories(self) -> None:
        if not self.series:
            raise ValueError("chart requires at least one series")
        for item in self.series:
            values = item.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError("each series requires non-empty values")
        if self.chart_type not in ("pie", "donut", "scatter", "bubble") and not self.categories:
            raise ValueError("chart requires categories")
        for item in self.series:
            series_type = str(item.get("type") or "").strip().lower()
            if series_type and series_type not in SERIES_TYPES:
                raise ValueError(f"unsupported series type: {series_type!r}")
            axis = str(item.get("axis") or "").strip().lower()
            if axis and axis not in SERIES_AXES:
                raise ValueError(f"unsupported series axis: {axis!r}")

    def _require_data_keys(self, *keys: str) -> None:
        for key in keys:
            value = self.data.get(key)
            if value in (None, "", [], {}):
                raise ValueError(f"{self.chart_type} requires data.{key}")


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
        "options": spec.options.to_dict(),
        "data": to_chart_data(spec.data),
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
            str(k): normalize_axis_config(v)
            for k, v in (data.get("axes") or {}).items()
            if isinstance(v, dict)
        } if isinstance(data.get("axes"), dict) else {},
        options=chart_options_from_dict(data.get("options") if isinstance(data.get("options"), dict) else {}),
        data=to_chart_data(data.get("data") if isinstance(data.get("data"), dict) else {}),
    )
    spec.validate()
    return spec


def to_chart_data(data: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if isinstance(value, dict):
            out[str(key)] = to_chart_data(value)
        elif isinstance(value, list):
            out[str(key)] = [to_chart_data(item) if isinstance(item, dict) else item for item in value]
        else:
            out[str(key)] = value
    return out
