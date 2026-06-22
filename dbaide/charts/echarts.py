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
from dbaide.charts.spec import ChartOptions, chart_spec_from_dict


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
_PIE_LIKE_TYPES = {"pie", "donut"}
_TREE_TYPES = {"treemap", "sunburst"}


def chart_spec_to_echarts_option(spec_dict: dict[str, Any], *, theme: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Convert a serialized ``ChartSpec`` into an ECharts option object."""
    spec = chart_spec_from_dict(spec_dict)
    theme_map = dict(theme or {})
    colors = list(theme_map.get("colors") or DEFAULT_PALETTE)
    text_color = str(theme_map.get("text") or "#d1d5db")
    muted = str(theme_map.get("muted") or "#9ca3af")
    border = str(theme_map.get("border") or "#374151")
    interactive = bool(theme_map.get("chartInteractive"))
    chart_type = spec.chart_type

    option: dict[str, Any] = {
        "backgroundColor": "transparent",
        "color": colors,
        "textStyle": {"color": text_color, "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, sans-serif"},
        "title": {"show": False},
    }

    if chart_type in _PIE_LIKE_TYPES:
        option.update(_build_pie_option(spec, text_color, muted))
        return option
    if chart_type == "funnel":
        option.update(_build_funnel_option(spec, text_color, muted))
        return option
    if chart_type in {"scatter", "bubble"}:
        option.update(_build_scatter_option(spec, muted, border, text_color))
        return option
    if chart_type == "heatmap":
        option.update(_build_heatmap_option(spec, muted, border))
        return option
    if chart_type == "radar":
        option.update(_build_radar_option(spec, text_color, muted))
        return option
    if chart_type == "gauge":
        option.update(_build_gauge_option(spec, text_color, muted, colors))
        return option
    if chart_type == "sankey":
        option.update(_build_sankey_option(spec, text_color, muted))
        return option
    if chart_type in _TREE_TYPES:
        option.update(_build_tree_option(spec, text_color))
        return option
    if chart_type == "tree":
        option.update(_build_tree_graph_option(spec, text_color, muted, border))
        return option
    if chart_type == "candlestick":
        option.update(_build_candlestick_option(spec, muted, border, interactive))
        return option
    if chart_type == "boxplot":
        option.update(_build_boxplot_option(spec, muted, border, interactive))
        return option
    if chart_type == "waterfall":
        option.update(_build_waterfall_option(spec, muted, border, text_color, interactive))
        return option

    option.update(_build_axis_chart_option(spec, muted, border, text_color, interactive))
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


def _build_axis_chart_option(
    spec: Any,
    muted: str,
    border: str,
    text_color: str,
    interactive: bool,
) -> dict[str, Any]:
    raw_categories = list(spec.categories)
    cat_display, cat_angle, cat_bottom = category_axis_layout(raw_categories)
    dense_categories = len(raw_categories) >= 10
    legend_position = _legend_position(spec.options)
    show_legend = len(spec.series) > 1
    chart_type = spec.chart_type

    if chart_type == "horizontal_bar":
        return {
            "tooltip": _tooltip_axis(),
            "legend": _legend(spec.series, text_color, show=show_legend, position=legend_position),
            "grid": _grid(
                bottom_extra=8,
                show_legend=show_legend,
                dual_axis=False,
                y_name="",
                x_name=spec.x_label or _axis_label(spec, "left"),
                horizontal=True,
                category_count=len(cat_display),
                legend_position=legend_position,
            ),
            "xAxis": _value_axis(
                spec.x_label or _axis_label(spec, "left"),
                muted,
                border,
                spec,
                "left",
                compact=True,
            ),
            "yAxis": _category_axis(
                cat_display,
                muted,
                border,
                spec=spec,
                side="y",
                horizontal=True,
                inverse=True,
                count=len(cat_display),
            ),
            "series": [
                _axis_series(
                    item,
                    count=len(raw_categories),
                    options=spec.options,
                    text_color=text_color,
                    series_type="bar",
                    horizontal=True,
                    stack="total" if spec.options.stacked else "",
                )
                for item in spec.series
            ],
        }

    auto_split = _auto_split_axes(spec)
    series_items = _apply_auto_split(spec.series) if auto_split else list(spec.series)
    uses_right = any(_series_axis(item) == "right" for item in series_items)
    left_label = _axis_label(spec, "left")
    right_label = _axis_label(spec, "right") if uses_right else ""
    if uses_right and not right_label:
        right_names = [str(s.get("name") or "") for s in series_items if _series_axis(s) == "right"]
        right_label = " / ".join(name for name in right_names if name)[:24]

    option: dict[str, Any] = {
        "tooltip": _tooltip_axis(),
        "legend": _legend(series_items, text_color, show=show_legend, position=legend_position),
        "grid": _grid(
            bottom_extra=cat_bottom,
            show_legend=show_legend,
            dual_axis=uses_right,
            y_name=left_label,
            x_name=spec.x_label,
            dense_zoom=interactive and dense_categories,
            legend_position=legend_position,
        ),
        "xAxis": _category_axis(
            cat_display,
            muted,
            border,
            spec=spec,
            side="x",
            name=spec.x_label,
            angle=cat_angle,
            count=len(cat_display),
        ),
        "yAxis": [_value_axis(left_label, muted, border, spec, "left", compact=True)],
    }
    if uses_right:
        option["yAxis"].append(_value_axis(right_label or " ", muted, border, spec, "right", compact=True))
    if interactive and dense_categories:
        option["dataZoom"] = _data_zoom(len(raw_categories), show_legend, legend_position=legend_position)

    if chart_type in {"stacked_bar"}:
        option["series"] = [
            _axis_series(item, count=len(raw_categories), options=spec.options, text_color=text_color, series_type="bar", stack="total")
            for item in series_items
        ]
    elif chart_type in {"line", "area", "stacked_area", "multi_axis_line"}:
        stack = "total" if chart_type == "stacked_area" or spec.options.stacked else ""
        option["series"] = [
            _axis_series(
                item,
                count=len(raw_categories),
                options=spec.options,
                text_color=text_color,
                series_type="line",
                stack=stack,
                area=chart_type in {"area", "stacked_area"} or _series_type(item) == "area",
            )
            for item in series_items
        ]
    elif chart_type == "combo":
        option["series"] = [
            _axis_series(
                item,
                count=len(raw_categories),
                options=spec.options,
                text_color=text_color,
                series_type=_series_kind(item, idx),
                area=_series_type(item) == "area",
            )
            for idx, item in enumerate(series_items)
        ]
    else:
        stack = "total" if spec.options.stacked else ""
        option["series"] = [
            _axis_series(item, count=len(raw_categories), options=spec.options, text_color=text_color, series_type="bar", stack=stack)
            for item in series_items
        ]
    return option


def _build_pie_option(spec: Any, text_color: str, muted: str) -> dict[str, Any]:
    values = _series_values(spec.series[0], len(spec.categories))
    inner = max(15, min(90, int(round(spec.options.donut_inner_ratio * 100))))
    radius: str | list[str] = "64%"
    if spec.chart_type == "donut":
        radius = [f"{inner}%", "72%"]
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "legend": _legend(spec.series, text_color, show=True, position=_legend_position(spec.options)),
        "series": [{
            "name": str(spec.series[0].get("name") or spec.title or "value"),
            "type": "pie",
            "radius": radius,
            "center": ["50%", "52%"],
            "roseType": "area" if spec.options.rose else False,
            "avoidLabelOverlap": True,
            "label": _pie_label(spec.options, text_color),
            "labelLine": {"lineStyle": {"color": muted}, "length": 12, "length2": 8},
            "data": [
                {"name": format_category_label(cat), "value": val}
                for cat, val in zip(spec.categories, values, strict=False)
            ],
        }],
    }


def _build_funnel_option(spec: Any, text_color: str, muted: str) -> dict[str, Any]:
    values = _series_values(spec.series[0], len(spec.categories))
    sort_order = spec.options.sort_order if spec.options.sort_order in {"ascending", "descending", "none"} else "descending"
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "legend": _legend(spec.series, text_color, show=False, position=_legend_position(spec.options)),
        "series": [{
            "name": str(spec.series[0].get("name") or spec.title or "value"),
            "type": "funnel",
            "left": "10%",
            "top": 16,
            "bottom": 16,
            "width": "80%",
            "minSize": "20%",
            "maxSize": "100%",
            "sort": sort_order,
            "gap": 2,
            "label": _series_label(text_color, spec.options, fallback_position="inside"),
            "labelLine": {"show": spec.options.label_mode == "outside", "length": 10, "lineStyle": {"color": muted}},
            "data": [
                {"name": format_category_label(cat), "value": val}
                for cat, val in zip(spec.categories, values, strict=False)
            ],
        }],
    }


def _build_scatter_option(spec: Any, muted: str, border: str, text_color: str) -> dict[str, Any]:
    points = list(spec.data.get("points") or [])
    bubble = spec.chart_type == "bubble"
    if points:
        data = []
        for idx, point in enumerate(points):
            x = _safe_float(point.get("x"))
            y = _safe_float(point.get("y"))
            name = str(point.get("name") or f"Point {idx + 1}")
            if bubble:
                raw_size = _safe_float(point.get("size"))
                data.append({"name": name, "value": [x, y, raw_size], "symbolSize": _bubble_size(raw_size)})
            else:
                data.append({"name": name, "value": [x, y]})
    else:
        item = spec.series[0]
        values = _series_values(item, len(spec.categories))
        data = []
        for idx, (x_raw, y) in enumerate(zip(spec.categories, values, strict=False)):
            if bubble:
                raw_size = 10.0
                data.append({"name": str(x_raw), "value": [_numeric_or_index(x_raw, idx), y, raw_size], "symbolSize": _bubble_size(raw_size)})
            else:
                data.append([_numeric_or_index(x_raw, idx), y])

    series_name = str((spec.series[0] if spec.series else {}).get("name") or spec.title or "value")
    series: dict[str, Any] = {
        "name": series_name,
        "type": "scatter",
        "data": data,
        "emphasis": {"focus": "series"},
    }
    if spec.options.show_labels:
        series["label"] = _series_label(text_color, spec.options, fallback_position="top")
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "legend": _legend(spec.series, text_color, show=False, position=_legend_position(spec.options)),
        "grid": _grid(
            bottom_extra=12,
            show_legend=False,
            dual_axis=False,
            y_name=spec.y_label or _axis_label(spec, "y") or _axis_label(spec, "left"),
            x_name=spec.x_label or _axis_label(spec, "x"),
            legend_position=_legend_position(spec.options),
        ),
        "xAxis": _value_axis(spec.x_label or _axis_label(spec, "x"), muted, border, spec, "x", compact=True),
        "yAxis": _value_axis(spec.y_label or _axis_label(spec, "y") or _axis_label(spec, "left"), muted, border, spec, "y", compact=True),
        "series": [series],
    }


def _build_heatmap_option(spec: Any, muted: str, border: str) -> dict[str, Any]:
    x_categories = [format_category_label(str(item)) for item in (spec.data.get("x_categories") or [])]
    y_categories = [format_category_label(str(item)) for item in (spec.data.get("y_categories") or [])]
    points = [
        [_safe_int(point[0]), _safe_int(point[1]), _safe_float(point[2])]
        for point in (spec.data.get("points") or [])
        if isinstance(point, list | tuple) and len(point) >= 3
    ]
    values = [point[2] for point in points]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0
    return {
        "tooltip": {"position": "top", "confine": True, "appendToBody": True},
        "grid": {"left": 64, "right": 24, "top": 18, "bottom": 42, "containLabel": True},
        "xAxis": _category_axis(x_categories, muted, border, spec=spec, side="x", count=len(x_categories)),
        "yAxis": _category_axis(y_categories, muted, border, spec=spec, side="y", horizontal=True, count=len(y_categories)),
        "visualMap": {
            "min": min_value,
            "max": max_value or 1.0,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 0,
            "textStyle": {"color": muted},
        },
        "series": [{
            "type": "heatmap",
            "data": points,
            "label": {"show": bool(spec.options.show_labels), "color": "#111827"},
            "emphasis": {"itemStyle": {"shadowBlur": 8, "shadowColor": "rgba(0,0,0,0.25)"}},
        }],
    }


def _build_radar_option(spec: Any, text_color: str, muted: str) -> dict[str, Any]:
    radar_shape = spec.options.radar_shape if spec.options.radar_shape in {"circle", "polygon"} else "polygon"
    indicators = []
    for indicator in (spec.data.get("indicators") or []):
        if isinstance(indicator, dict):
            indicators.append({
                "name": str(indicator.get("name") or "—"),
                "max": max(1.0, _safe_float(indicator.get("max")) or 1.0),
            })
    radar_series = [
        {"name": str(item.get("name") or "—"), "value": [_safe_float(v) for v in (item.get("value") or [])]}
        for item in (spec.data.get("radar_series") or [])
        if isinstance(item, dict)
    ]
    legend_payload = [{"name": item["name"]} for item in radar_series]
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "legend": _legend(legend_payload, text_color, show=len(radar_series) > 1, position=_legend_position(spec.options)),
        "radar": {
            "shape": radar_shape,
            "radius": "68%",
            "indicator": indicators,
            "axisName": {"color": muted},
            "splitLine": {"lineStyle": {"color": "rgba(156,163,175,0.35)"}},
            "splitArea": {"areaStyle": {"color": ["rgba(59,130,246,0.05)", "rgba(59,130,246,0.01)"]}},
        },
        "series": [{
            "type": "radar",
            "data": radar_series,
            "areaStyle": {"opacity": spec.options.area_opacity * 0.85},
            "symbol": "circle",
            "symbolSize": 5,
            "label": _series_label(text_color, spec.options, fallback_position="top") if spec.options.show_labels else {"show": False},
        }],
    }


def _build_gauge_option(spec: Any, text_color: str, muted: str, colors: list[str]) -> dict[str, Any]:
    value = _safe_float(spec.data.get("value"))
    name = str(spec.data.get("name") or spec.title or "value")
    minimum = spec.options.gauge_min if spec.options.gauge_min is not None else 0.0
    maximum = spec.options.gauge_max if spec.options.gauge_max is not None else max(100.0, value)
    if maximum <= minimum:
        maximum = minimum + 1.0
    ratio = max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
    segments = [[ratio, colors[0]], [1.0, "rgba(156,163,175,0.18)"]]
    detail_formatter = "{value}"
    if spec.options.gauge_target is not None:
        detail_formatter = "{value} / " + str(spec.options.gauge_target)
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "series": [{
            "type": "gauge",
            "min": minimum,
            "max": maximum,
            "progress": {"show": True, "width": 12},
            "axisLine": {"lineStyle": {"width": 12, "color": segments}},
            "axisTick": {"show": False},
            "splitLine": {"distance": -12, "length": 10, "lineStyle": {"color": muted}},
            "axisLabel": {"color": muted, "distance": 18},
            "pointer": {"width": 4},
            "anchor": {"show": True, "showAbove": True, "size": 12},
            "title": {"show": True, "offsetCenter": [0, "78%"], "color": muted},
            "detail": {"valueAnimation": False, "fontSize": 24, "color": text_color, "formatter": detail_formatter},
            "data": [{"value": value, "name": name}],
        }],
    }


def _build_sankey_option(spec: Any, text_color: str, muted: str) -> dict[str, Any]:
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "series": [{
            "type": "sankey",
            "layout": "none",
            "emphasis": {"focus": "adjacency"},
            "nodeAlign": spec.options.node_align or "justify",
            "draggable": False,
            "lineStyle": {"color": "gradient", "curveness": 0.5, "opacity": 0.35},
            "label": {"color": text_color},
            "data": [dict(node) for node in (spec.data.get("nodes") or []) if isinstance(node, dict)],
            "links": [dict(link) for link in (spec.data.get("links") or []) if isinstance(link, dict)],
        }],
        "textStyle": {"color": muted},
    }


def _build_tree_option(spec: Any, text_color: str) -> dict[str, Any]:
    data = [dict(item) for item in (spec.data.get("tree") or []) if isinstance(item, dict)]
    if spec.chart_type == "treemap":
        return {
            "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
            "series": [{
                "type": "treemap",
                "leafDepth": 2,
                "roam": False,
                "breadcrumb": {"show": True},
                "label": {"show": True, "color": text_color},
                "upperLabel": {"show": True, "height": 20, "color": text_color},
                "data": data,
            }],
        }
    return {
        "tooltip": {"trigger": "item", "confine": True, "appendToBody": True},
        "series": [{
            "type": "sunburst",
            "radius": ["12%", "86%"],
            "sort": None,
            "label": {"rotate": "radial", "color": text_color},
            "data": data,
        }],
    }


def _build_tree_graph_option(spec: Any, text_color: str, muted: str, border: str) -> dict[str, Any]:
    """Node-link tree (ECharts `tree` series) — a classic hierarchy diagram.

    ``data.tree`` is the same hierarchical ``{name, value?, children?}`` shape as
    treemap/sunburst, but rendered as an orthogonal left-to-right tree. A single root
    is expected; if several top-level nodes are supplied they are hung under a
    synthetic root so the diagram stays a valid tree.
    """
    nodes = [dict(item) for item in (spec.data.get("tree") or []) if isinstance(item, dict)]
    if len(nodes) == 1:
        root = nodes[0]
    else:
        root = {"name": spec.title or "root", "children": nodes}
    orient = "LR"
    opts = getattr(spec, "options", None)
    if opts is not None and str(getattr(opts, "orientation", "") or "").lower() in {"tb", "vertical"}:
        orient = "TB"
    return {
        "tooltip": {"trigger": "item", "triggerOn": "mousemove", "confine": True, "appendToBody": True},
        "series": [{
            "type": "tree",
            "data": [root],
            "top": "2%",
            "bottom": "2%",
            "left": "7%",
            "right": "16%",
            "orient": orient,
            "symbol": "circle",
            "symbolSize": 9,
            "roam": False,
            "expandAndCollapse": False,
            "initialTreeDepth": -1,
            "lineStyle": {"color": border, "width": 1.2, "curveness": 0.5},
            "itemStyle": {"color": "#3b82f6", "borderColor": "#3b82f6"},
            "label": {
                "position": "right" if orient == "LR" else "top",
                "verticalAlign": "middle",
                "align": "left" if orient == "LR" else "center",
                "color": text_color,
                "fontSize": 12,
                "distance": 7,
            },
            "leaves": {
                "label": {
                    "position": "right" if orient == "LR" else "bottom",
                    "verticalAlign": "middle",
                    "align": "left" if orient == "LR" else "center",
                    "color": muted,
                },
            },
            "emphasis": {"focus": "descendant"},
            "animationDuration": 450,
            "animationDurationUpdate": 600,
        }],
    }


def _build_candlestick_option(spec: Any, muted: str, border: str, interactive: bool) -> dict[str, Any]:
    raw_categories = list(spec.categories)
    cat_display, cat_angle, cat_bottom = category_axis_layout(raw_categories)
    dense_categories = len(raw_categories) >= 10
    return {
        "tooltip": _tooltip_axis(),
        "grid": _grid(
            bottom_extra=cat_bottom,
            show_legend=False,
            dual_axis=False,
            y_name=_axis_label(spec, "left"),
            x_name=spec.x_label,
            dense_zoom=interactive and dense_categories,
            legend_position=_legend_position(spec.options),
        ),
        "xAxis": _category_axis(cat_display, muted, border, spec=spec, side="x", name=spec.x_label, angle=cat_angle, count=len(cat_display)),
        "yAxis": [_value_axis(_axis_label(spec, "left"), muted, border, spec, "left", compact=True)],
        "series": [{
            "type": "candlestick",
            "name": spec.title or "OHLC",
            "data": [[_safe_float(v) for v in item[:4]] for item in (spec.data.get("ohlc") or []) if isinstance(item, list | tuple)],
        }],
        **({"dataZoom": _data_zoom(len(raw_categories), False, legend_position=_legend_position(spec.options))} if interactive and dense_categories else {}),
    }


def _build_boxplot_option(spec: Any, muted: str, border: str, interactive: bool) -> dict[str, Any]:
    raw_categories = list(spec.categories)
    cat_display, cat_angle, cat_bottom = category_axis_layout(raw_categories)
    dense_categories = len(raw_categories) >= 10
    series = [{
        "name": spec.title or "boxplot",
        "type": "boxplot",
        "data": [[_safe_float(v) for v in box[:5]] for box in (spec.data.get("boxes") or []) if isinstance(box, list | tuple)],
        "tooltip": {"formatter": "{b}<br/>min: {c0}<br/>Q1: {c1}<br/>median: {c2}<br/>Q3: {c3}<br/>max: {c4}"},
    }]
    outliers = [list(item[:2]) for item in (spec.data.get("outliers") or []) if isinstance(item, list | tuple) and len(item) >= 2]
    if outliers:
        series.append({"name": "outlier", "type": "scatter", "data": outliers, "symbolSize": 8})
    option = {
        "tooltip": _tooltip_axis(),
        "legend": _legend([], muted, show=False, position=_legend_position(spec.options)),
        "grid": _grid(
            bottom_extra=cat_bottom,
            show_legend=False,
            dual_axis=False,
            y_name=_axis_label(spec, "left"),
            x_name=spec.x_label,
            dense_zoom=interactive and dense_categories,
            legend_position=_legend_position(spec.options),
        ),
        "xAxis": _category_axis(cat_display, muted, border, spec=spec, side="x", name=spec.x_label, angle=cat_angle, count=len(cat_display)),
        "yAxis": [_value_axis(_axis_label(spec, "left"), muted, border, spec, "left", compact=True)],
        "series": series,
    }
    if interactive and dense_categories:
        option["dataZoom"] = _data_zoom(len(raw_categories), False, legend_position=_legend_position(spec.options))
    return option


def _build_waterfall_option(spec: Any, muted: str, border: str, text_color: str, interactive: bool) -> dict[str, Any]:
    raw_categories = list(spec.categories)
    cat_display, cat_angle, cat_bottom = category_axis_layout(raw_categories)
    dense_categories = len(raw_categories) >= 10
    values = _series_values(spec.series[0], len(raw_categories))
    base_values: list[float] = []
    actual_values: list[dict[str, Any]] = []
    running = 0.0
    for value in values:
        if value >= 0:
            base_values.append(running)
            actual_values.append({"value": value, "itemStyle": {"color": "#22c55e"}})
        else:
            base_values.append(running + value)
            actual_values.append({"value": abs(value), "itemStyle": {"color": "#ef4444"}})
        running += value
    option = {
        "tooltip": _tooltip_axis(),
        "legend": _legend(spec.series, text_color, show=False, position=_legend_position(spec.options)),
        "grid": _grid(
            bottom_extra=cat_bottom,
            show_legend=False,
            dual_axis=False,
            y_name=_axis_label(spec, "left"),
            x_name=spec.x_label,
            dense_zoom=interactive and dense_categories,
            legend_position=_legend_position(spec.options),
        ),
        "xAxis": _category_axis(cat_display, muted, border, spec=spec, side="x", name=spec.x_label, angle=cat_angle, count=len(cat_display)),
        "yAxis": [_value_axis(_axis_label(spec, "left"), muted, border, spec, "left", compact=True)],
        "series": [
            {
                "type": "bar",
                "stack": "total",
                "silent": True,
                "itemStyle": {"color": "rgba(0,0,0,0)"},
                "emphasis": {"itemStyle": {"color": "rgba(0,0,0,0)"}},
                "data": base_values,
            },
            {
                "name": str(spec.series[0].get("name") or spec.title or "value"),
                "type": "bar",
                "stack": "total",
                "barMaxWidth": spec.options.bar_max_width,
                "label": _series_label(text_color, spec.options, fallback_position="top"),
                "data": actual_values,
            },
        ],
    }
    if interactive and dense_categories:
        option["dataZoom"] = _data_zoom(len(raw_categories), False, legend_position=_legend_position(spec.options))
    return option


def _tooltip_axis() -> dict[str, Any]:
    return {
        "trigger": "axis",
        "confine": True,
        "appendToBody": True,
        "axisPointer": {"type": "cross", "label": {"backgroundColor": "#6b7280"}},
    }


def _legend(
    series: list[dict[str, Any]],
    text_color: str,
    *,
    show: bool,
    position: str,
) -> dict[str, Any]:
    legend: dict[str, Any] = {
        "show": show,
        "type": "scroll",
        "itemGap": 12,
        "itemWidth": 12,
        "itemHeight": 8,
        "textStyle": {"color": text_color, "fontSize": 11},
        "pageIconColor": text_color,
        "pageTextStyle": {"color": text_color},
    }
    if position == "top":
        legend.update({"top": 4, "left": "center"})
    elif position == "left":
        legend.update({"left": 0, "top": "middle", "orient": "vertical"})
    elif position == "right":
        legend.update({"right": 0, "top": "middle", "orient": "vertical"})
    else:
        legend.update({"bottom": 4, "left": "center"})
    return legend


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
    legend_position: str = "bottom",
) -> dict[str, Any]:
    left = 58 if y_name else 48
    if dual_axis:
        left = max(left, 52)
    right = 52 if dual_axis else 16
    top = 42 if y_name else 28
    bottom = 36 + bottom_extra
    if x_name:
        bottom += 8
    if dense_zoom:
        bottom += 28
    if horizontal:
        left = max(48, min(160, 12 * max(category_count, 1) + 24))
    if show_legend:
        if legend_position == "top":
            top += 28
        elif legend_position in {"left", "right"}:
            if legend_position == "left":
                left += 96
            else:
                right += 96
        else:
            bottom += 28
    return {"left": left, "right": right, "top": top, "bottom": bottom, "containLabel": True}


def _category_axis(
    categories: list[str],
    muted: str,
    border: str,
    *,
    spec: Any | None = None,
    side: str = "x",
    name: str = "",
    inverse: bool = False,
    angle: int = 0,
    count: int = 0,
    horizontal: bool = False,
) -> dict[str, Any]:
    cfg = _axis_config(spec, side)
    n = count or len(categories)
    interval = _label_interval(n, angle)
    axis: dict[str, Any] = {
        "type": "category",
        "data": categories,
        "inverse": bool(inverse or cfg.get("inverse")),
        "name": _truncate_axis_name(name or str(cfg.get("label") or "")),
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
    cfg = _axis_config(spec, side)
    fmt = str(cfg.get("format") or "").strip().lower()
    axis_type = "log" if cfg.get("log") else "value"
    axis: dict[str, Any] = {
        "type": axis_type,
        "name": _truncate_axis_name(label or str(cfg.get("label") or "")),
        "nameTextStyle": {"color": muted, "padding": [0, 0, 4, 0]},
        "nameLocation": "end",
        "nameGap": 14,
        "axisLabel": {"color": muted, "hideOverlap": True, "margin": 8},
        "splitLine": {"lineStyle": {"color": border, "opacity": 0.45}},
        "axisLine": {"show": False},
        "scale": False,
    }
    if cfg.get("min") is not None:
        axis["min"] = cfg["min"]
    if cfg.get("max") is not None:
        axis["max"] = cfg["max"]
    if cfg.get("inverse"):
        axis["inverse"] = True
    if compact and fmt not in {"percent"}:
        axis["_compactValues"] = True
        axis["_valueFormat"] = fmt or "number"
    if fmt == "percent":
        axis["axisLabel"]["formatter"] = "{value}%"
    elif fmt == "currency" and not compact:
        axis["axisLabel"]["formatter"] = "${value}"
    return axis


def _data_zoom(count: int, show_legend: bool, *, legend_position: str) -> list[dict[str, Any]]:
    window = min(14, count)
    start = max(0, 100 - int(100 * window / max(count, 1)))
    bottom = 8 + (28 if show_legend and legend_position == "bottom" else 0)
    return [
        {
            "type": "inside",
            "start": start,
            "end": 100,
            "zoomOnMouseWheel": True,
            "moveOnMouseMove": True,
        },
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
    count: int,
    options: ChartOptions,
    text_color: str,
    series_type: str,
    stack: str = "",
    area: bool = False,
    horizontal: bool = False,
) -> dict[str, Any]:
    values = _series_values(item, count)
    out: dict[str, Any] = {
        "name": str(item.get("name") or "value"),
        "type": series_type,
        "data": values,
        "yAxisIndex": 1 if _series_axis(item) == "right" else 0,
        "emphasis": {"focus": "series"},
        "connectNulls": False,
    }
    if stack:
        out["stack"] = stack
    label = _series_label(text_color, options, fallback_position="right" if horizontal else ("inside" if area else "top"))
    if label["show"]:
        out["label"] = label
    if series_type == "line":
        out["symbolSize"] = 5 if count > 20 else 6
        out["showSymbol"] = options.show_symbols if options.show_symbols is not None else count <= 24
        out["smooth"] = bool(options.smooth) if options.smooth is not None else False
        if options.step and options.step != "none":
            out["step"] = options.step
    if area:
        out["areaStyle"] = {"opacity": options.area_opacity}
    if series_type == "bar":
        out["barMaxWidth"] = options.bar_max_width
    return out


def _series_label(text_color: str, options: ChartOptions, *, fallback_position: str) -> dict[str, Any]:
    show = bool(options.show_labels)
    position = options.label_mode or fallback_position
    if options.label_mode == "none":
        show = False
    return {"show": show, "position": position, "color": text_color, "fontSize": 11}


def _pie_label(options: ChartOptions, text_color: str) -> dict[str, Any]:
    if options.show_labels is False or options.label_mode == "none":
        return {"show": False}
    position = "inside" if options.label_mode in {"inside", "center"} else "outside"
    return {
        "show": True,
        "position": position,
        "color": text_color,
        "formatter": "{b}\n{d}%",
        "overflow": "truncate",
        "width": 120,
    }


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
    if spec.chart_type in {"combo", "multi_axis_line", "scatter", "bubble", "pie", "donut", "horizontal_bar"}:
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
        copy["axis"] = "right" if threshold > 0 and 0 < peak < threshold else "left"
        out.append(copy)
    return out


def _series_values(item: dict[str, Any], count: int = 0) -> list[float]:
    values = [_safe_float(v) for v in (item.get("values") or [])]
    if count > 0:
        values = values[:count]
        if len(values) < count:
            values.extend([0.0] * (count - len(values)))
    return values


def _bubble_size(value: Any) -> float:
    raw = abs(_safe_float(value))
    if raw <= 0:
        return 10.0
    return max(8.0, min(36.0, math.sqrt(raw) * 3.0))


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
    if side in {"left", "y"}:
        return str(getattr(spec, "y_label", "") or "")
    if side == "x":
        return str(getattr(spec, "x_label", "") or "")
    return ""


def _legend_position(options: ChartOptions) -> str:
    position = str(options.legend_position or "").strip().lower()
    return position if position in {"top", "bottom", "left", "right"} else "bottom"
