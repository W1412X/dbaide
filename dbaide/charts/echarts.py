"""ECharts option generation for DBAide chart specs.

This module is deliberately GUI-free: it maps the stable ``ChartSpec`` payload
produced by the agent into Apache ECharts option JSON. The desktop layer only has
to host the generated option inside Qt WebEngine.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from dbaide.charts.labels import category_axis_layout, format_category_label
from dbaide.charts.spec import chart_spec_from_dict


DEFAULT_PALETTE = [
    "#3b82f6",
    "#22c55e",
    "#8b5cf6",
    "#0ea5e9",
    "#14b8a6",
    "#eab308",
    "#ef4444",
    "#f97316",
]

_MAGNITUDE_SPLIT_RATIO = 40.0


def chart_spec_to_echarts_option(spec_dict: dict[str, Any], *, theme: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Convert a serialized ``ChartSpec`` into an ECharts option object."""
    spec = chart_spec_from_dict(spec_dict)
    theme = dict(theme or {})
    colors = list(theme.get("colors") or DEFAULT_PALETTE)
    text_color = str(theme.get("text") or "#d1d5db")
    muted = str(theme.get("muted") or "#9ca3af")
    border = str(theme.get("border") or "#374151")

    categories = [format_category_label(c) for c in spec.categories]
    raw_categories = list(spec.categories)
    show_legend = len(spec.series) > 1
    cat_display, cat_angle, cat_bottom = category_axis_layout(raw_categories)

    option: dict[str, Any] = {
        "backgroundColor": "transparent",
        "color": colors,
        "textStyle": {"color": text_color, "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, sans-serif"},
        "title": {"show": False},
        "tooltip": _tooltip_axis(),
        "legend": _legend(spec.series, text_color, show=show_legend),
    }
    chart_type = spec.chart_type

    if chart_type in {"pie", "donut"}:
        values = _series_values(spec.series[0], len(categories))
        option["tooltip"] = {"trigger": "item", "confine": True, "appendToBody": True}
        option["series"] = [{
            "name": str(spec.series[0].get("name") or spec.title or "value"),
            "type": "pie",
            "radius": ["42%", "68%"] if chart_type == "donut" else "62%",
            "center": ["50%", "52%"],
            "avoidLabelOverlap": True,
            "label": {"color": text_color, "formatter": "{b}\n{d}%", "overflow": "truncate", "width": 120},
            "labelLine": {"lineStyle": {"color": muted}, "length": 12, "length2": 8},
            "data": [
                {"name": cat, "value": val}
                for cat, val in zip(categories, values, strict=False)
            ],
        }]
        return option

    if chart_type == "scatter":
        item = spec.series[0]
        y_values = _series_values(item, len(categories))
        data = []
        for idx, (x_raw, y) in enumerate(zip(spec.categories, y_values, strict=False)):
            x = _numeric_or_index(x_raw, idx)
            data.append([x, y])
        option["grid"] = _grid(
            bottom_extra=cat_bottom,
            show_legend=show_legend,
            dual_axis=False,
            y_name=_axis_label(spec, "left"),
            x_name=spec.x_label,
        )
        option["xAxis"] = _value_axis(spec.x_label, muted, border, spec, "left", compact=True)
        option["yAxis"] = _value_axis(_axis_label(spec, "left"), muted, border, spec, "left", compact=True)
        option["series"] = [{
            "name": str(item.get("name") or "value"),
            "type": "scatter",
            "symbolSize": 8,
            "data": data,
        }]
        return option

    if chart_type == "horizontal_bar":
        option["grid"] = _grid(
            bottom_extra=8,
            show_legend=show_legend,
            dual_axis=False,
            y_name="",
            x_name=spec.x_label or _axis_label(spec, "left"),
            horizontal=True,
            category_count=len(cat_display),
        )
        option["xAxis"] = _value_axis(
            spec.x_label or _axis_label(spec, "left"),
            muted,
            border,
            spec,
            "left",
            compact=True,
        )
        option["yAxis"] = _category_axis(
            cat_display,
            muted,
            border,
            raw_categories=raw_categories,
            horizontal=True,
            inverse=True,
        )
        option["series"] = [
            _axis_series(item, chart_type="bar", stack="", count=len(categories))
            for item in spec.series
        ]
        return option

    auto_split = _auto_split_axes(spec)
    uses_right = any(_series_axis(item) == "right" for item in spec.series) or auto_split
    series_items = list(spec.series)
    if auto_split:
        series_items = _apply_auto_split(series_items)
        uses_right = any(_series_axis(item) == "right" for item in series_items)
    y_left = _axis_label(spec, "left")
    y_right = _axis_label(spec, "right") if uses_right else ""
    if uses_right and not y_right:
        right_names = [str(s.get("name") or "") for s in series_items if _series_axis(s) == "right"]
        y_right = " / ".join(n for n in right_names if n)[:24]

    option["grid"] = _grid(
        bottom_extra=cat_bottom,
        show_legend=show_legend,
        dual_axis=uses_right,
        y_name=y_left,
        x_name=spec.x_label,
        dense_zoom=len(raw_categories) >= 10,
    )
    option["xAxis"] = _category_axis(
        cat_display,
        muted,
        border,
        name=spec.x_label,
        angle=cat_angle,
        count=len(cat_display),
    )
    y_axes = [_value_axis(y_left, muted, border, spec, "left", compact=True)]
    if uses_right:
        y_axes.append(_value_axis(y_right or " ", muted, border, spec, "right", compact=True))
    option["yAxis"] = y_axes

    if len(raw_categories) >= 10:
        option["dataZoom"] = _data_zoom(len(raw_categories), cat_bottom, show_legend)

    if chart_type == "stacked_bar":
        option["series"] = [
            _axis_series(item, chart_type="bar", stack="total", count=len(categories))
            for item in series_items
        ]
    elif chart_type == "stacked_area":
        option["series"] = [
            _axis_series(item, chart_type="line", stack="total", area=True, count=len(categories))
            for item in series_items
        ]
    elif chart_type in {"line", "area", "multi_axis_line"}:
        option["series"] = [
            _axis_series(item, chart_type="line", area=(chart_type == "area"), count=len(categories))
            for item in series_items
        ]
    elif chart_type == "combo":
        option["series"] = [
            _axis_series(
                item,
                chart_type=_series_kind(item, idx),
                area=_series_type(item) == "area",
                count=len(categories),
            )
            for idx, item in enumerate(series_items)
        ]
    else:
        option["series"] = [
            _axis_series(item, chart_type="bar", count=len(categories))
            for item in series_items
        ]
    return option


def render_echarts_html(
    spec_dict: dict[str, Any],
    *,
    theme: Mapping[str, Any] | None = None,
    echarts_src: str | None = None,
) -> str:
    from dbaide.rendering.vendor_scripts import echarts_script_src

    option = chart_spec_to_echarts_option(spec_dict, theme=theme)
    option_json = json.dumps(option, ensure_ascii=False, separators=(",", ":"))
    src = str(echarts_src if echarts_src is not None else echarts_script_src())
    src_json = json.dumps(src, ensure_ascii=False)
    theme_map = dict(theme or {})
    bg = str(theme_map.get("bg") or theme_map.get("panel") or "#07080a")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; background: {bg}; overflow: hidden; }}
    body {{ font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; }}
    #chart {{ width: 100%; height: 100%; min-height: 240px; }}
    #error {{ display:none; color:#ef4444; padding:16px; font-size:13px; }}
  </style>
  <script src={src_json}></script>
</head>
<body>
  <div id="chart"></div>
  <div id="error">ECharts failed to load. Install the GUI WebEngine dependency and ensure chart assets are reachable.</div>
  <script>
    const option = {option_json};

    function compactAxisValue(value) {{
      const n = Number(value);
      if (!Number.isFinite(n)) return value;
      const abs = Math.abs(n);
      if (abs >= 1e8) return (n / 1e8).toFixed(1).replace(/\\.0$/, '') + '\\u4ebf';
      if (abs >= 1e4) return (n / 1e4).toFixed(1).replace(/\\.0$/, '') + '\\u4e07';
      if (abs >= 1000) return (n / 1000).toFixed(1).replace(/\\.0$/, '') + 'k';
      if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
      return n.toFixed(2).replace(/0+$/, '').replace(/\\.$/, '');
    }}

    function applyRuntimeFormatters(root) {{
      const patch = (axis) => {{
        if (!axis || axis.type !== 'value') return;
        axis.axisLabel = axis.axisLabel || {{}};
        if (axis._compactValues) {{
          if (axis._valueFormat === 'currency') {{
            axis.axisLabel.formatter = (v) => '$' + compactAxisValue(v);
          }} else if (axis._valueFormat !== 'percent') {{
            axis.axisLabel.formatter = compactAxisValue;
          }}
          delete axis._compactValues;
          delete axis._valueFormat;
        }}
        if (axis.name) {{
          axis.nameGap = Math.max(axis.nameGap || 0, 14);
          axis.nameLocation = axis.nameLocation || 'end';
          axis.nameTruncate = {{ maxWidth: 96, ellipsis: '…' }};
        }}
      }};
      const axes = Array.isArray(root.yAxis) ? root.yAxis : [root.yAxis];
      axes.forEach(patch);
      const xAxes = Array.isArray(root.xAxis) ? root.xAxis : [root.xAxis];
      xAxes.forEach((axis) => {{
        if (axis && axis.type === 'value') patch(axis);
      }});
    }}

    function render() {{
      if (!window.echarts) {{
        document.getElementById('chart').style.display = 'none';
        document.getElementById('error').style.display = 'block';
        return;
      }}
      applyRuntimeFormatters(option);
      const el = document.getElementById('chart');
      const chart = echarts.init(el, null, {{ renderer: 'canvas' }});
      chart.setOption(option, true);
      const resize = () => {{ try {{ chart.resize(); }} catch (e) {{}} }};
      window.addEventListener('resize', resize);
      if (window.ResizeObserver) {{
        new ResizeObserver(resize).observe(el);
      }}
      setTimeout(resize, 0);
      requestAnimationFrame(resize);
    }}
    render();
  </script>
</body>
</html>"""


def _tooltip_axis() -> dict[str, Any]:
    return {
        "trigger": "axis",
        "confine": True,
        "appendToBody": True,
        "axisPointer": {"type": "cross", "label": {"backgroundColor": "#6b7280"}},
    }


def _legend(series: list[dict[str, Any]], text_color: str, *, show: bool) -> dict[str, Any]:
    return {
        "show": show,
        "type": "scroll",
        "bottom": 4,
        "left": "center",
        "itemGap": 12,
        "itemWidth": 12,
        "itemHeight": 8,
        "textStyle": {"color": text_color, "fontSize": 11},
        "pageIconColor": text_color,
        "pageTextStyle": {"color": text_color},
    }


def _grid(
    *,
    bottom_extra: int,
    show_legend: bool,
    dual_axis: bool,
    y_name: str,
    x_name: str = "",
    horizontal: bool = False,
    category_count: int = 0,
    dense_zoom: bool = False,
) -> dict[str, Any]:
    left = 58 if y_name else 48
    if dual_axis:
        left = max(left, 52)
    right = 52 if dual_axis else 16
    top = 42 if y_name else 28
    bottom = 36 + bottom_extra
    if show_legend:
        bottom += 28
    if x_name:
        bottom += 8
    if dense_zoom:
        bottom += 28
    if horizontal:
        left = max(48, min(160, 12 * max(category_count, 1) + 24))
    return {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "containLabel": True,
    }


def _category_axis(
    categories: list[str],
    muted: str,
    border: str,
    *,
    name: str = "",
    inverse: bool = False,
    angle: int = 0,
    count: int = 0,
    raw_categories: list[str] | None = None,
    horizontal: bool = False,
) -> dict[str, Any]:
    n = count or len(categories)
    interval = _label_interval(n, angle)
    axis: dict[str, Any] = {
        "type": "category",
        "data": categories,
        "inverse": bool(inverse),
        "name": _truncate_axis_name(name),
        "nameLocation": "middle",
        "nameGap": 28 if not horizontal else 12,
        "nameTextStyle": {"color": muted},
        "axisLabel": {
            "color": muted,
            "hideOverlap": True,
            "interval": interval,
            "rotate": angle,
            "overflow": "truncate",
            "width": 88 if angle != 0 else 72,
        },
        "axisLine": {"lineStyle": {"color": border}},
        "axisTick": {"alignWithLabel": True},
    }
    if horizontal:
        axis["axisLabel"]["width"] = 120
    return axis


def _value_axis(
    label: str,
    muted: str,
    border: str,
    spec: Any = None,
    side: str = "left",
    *,
    compact: bool = False,
) -> dict[str, Any]:
    fmt = _axis_format(spec, side)
    axis: dict[str, Any] = {
        "type": "value",
        "name": _truncate_axis_name(label),
        "nameTextStyle": {"color": muted, "padding": [0, 0, 4, 0]},
        "nameLocation": "end",
        "nameGap": 14,
        "axisLabel": {"color": muted, "hideOverlap": True, "margin": 8},
        "splitLine": {"lineStyle": {"color": border, "opacity": 0.45}},
        "axisLine": {"show": False},
        "scale": False,
    }
    if compact and fmt not in {"percent"}:
        axis["_compactValues"] = True
        axis["_valueFormat"] = fmt or "number"
    if fmt == "percent":
        axis["axisLabel"]["formatter"] = "{value}%"
    elif fmt == "currency" and not compact:
        axis["axisLabel"]["formatter"] = "${value}"
    return axis


def _data_zoom(count: int, bottom_extra: int, show_legend: bool) -> list[dict[str, Any]]:
    window = min(14, count)
    start = max(0, 100 - int(100 * window / max(count, 1)))
    bottom = 8 + (28 if show_legend else 0)
    return [
        {"type": "inside", "start": start, "end": 100, "zoomOnMouseWheel": True, "moveOnMouseMove": True},
        {
            "type": "slider",
            "start": start,
            "end": 100,
            "height": 16,
            "bottom": bottom,
            "borderColor": "transparent",
            "fillerColor": "rgba(59,130,246,0.15)",
            "handleSize": 12,
        },
    ]


def _label_interval(count: int, angle: int) -> int | str:
    if count <= 8:
        return 0
    if angle != 0:
        return "auto"
    if count <= 14:
        return 0
    return max(1, count // 12)


def _truncate_axis_name(name: str, *, max_len: int = 14) -> str:
    text = " ".join(str(name or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _axis_series(
    item: dict[str, Any],
    *,
    chart_type: str,
    count: int,
    stack: str = "",
    area: bool = False,
) -> dict[str, Any]:
    values = _series_values(item, count)
    out: dict[str, Any] = {
        "name": str(item.get("name") or "value"),
        "type": chart_type,
        "data": values,
        "yAxisIndex": 1 if _series_axis(item) == "right" else 0,
        "emphasis": {"focus": "series"},
        "connectNulls": False,
    }
    if stack:
        out["stack"] = stack
    if area:
        out["areaStyle"] = {"opacity": 0.18}
        out["smooth"] = True
    if chart_type == "line":
        out["symbolSize"] = 5 if count > 20 else 6
        out["showSymbol"] = count <= 24
        out["smooth"] = True
    if chart_type == "bar":
        out["barMaxWidth"] = 42
    return out


def _series_kind(item: dict[str, Any], index: int) -> str:
    raw = _series_type(item)
    if raw in {"bar", "line"}:
        return raw
    if raw == "area":
        return "line"
    return "bar" if index == 0 else "line"


def _series_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or "").strip().lower()


def _series_axis(item: dict[str, Any]) -> str:
    return "right" if str(item.get("axis") or "").strip().lower() == "right" else "left"


def _series_peak(item: dict[str, Any]) -> float:
    values = [_safe_float(v) for v in (item.get("values") or [])]
    positives = [abs(v) for v in values if abs(v) > 1e-9]
    return max(positives) if positives else 0.0


def _auto_split_axes(spec: Any) -> bool:
    if spec.chart_type in {"combo", "multi_axis_line", "scatter", "pie", "donut", "horizontal_bar"}:
        return False
    if any(_series_axis(item) == "right" for item in spec.series):
        return False
    if len(spec.series) < 2:
        return False
    peaks = [_series_peak(item) for item in spec.series]
    peaks = [p for p in peaks if p > 0]
    if len(peaks) < 2:
        return False
    ratio = max(peaks) / max(min(peaks), 1e-9)
    return ratio >= _MAGNITUDE_SPLIT_RATIO


def _apply_auto_split(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peaks = [_series_peak(item) for item in series]
    max_peak = max(peaks) if peaks else 0.0
    threshold = max_peak / _MAGNITUDE_SPLIT_RATIO if max_peak > 0 else 0.0
    out: list[dict[str, Any]] = []
    for item, peak in zip(series, peaks, strict=False):
        copy = dict(item)
        if threshold > 0 and 0 < peak < threshold:
            copy["axis"] = "right"
        else:
            copy["axis"] = "left"
        out.append(copy)
    return out


def _series_values(item: dict[str, Any], count: int = 0) -> list[float]:
    values = [_safe_float(v) for v in (item.get("values") or [])]
    if count > 0:
        values = values[:count]
        if len(values) < count:
            values.extend([0.0] * (count - len(values)))
    return values


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _numeric_or_index(value: Any, index: int) -> float:
    try:
        out = float(value)
        if math.isfinite(out):
            return out
    except (TypeError, ValueError):
        pass
    return float(index)


def _axis_config(spec: Any, side: str) -> dict[str, Any]:
    axes = getattr(spec, "axes", None) or {}
    raw = axes.get(side) if isinstance(axes, dict) else None
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _axis_label(spec: Any, side: str) -> str:
    cfg = _axis_config(spec, side)
    label = str(cfg.get("label") or "").strip()
    if label:
        return label
    return str(getattr(spec, "y_label", "") or "") if side == "left" else ""


def _axis_format(spec: Any, side: str) -> str:
    return str(_axis_config(spec, side).get("format") or "").strip().lower() if spec is not None else ""
