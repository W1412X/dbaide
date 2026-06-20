"""Dedicated chart-planning agent (separate from the main Ask loop)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.agent.prompts.chart_agent import chart_agent_system_prompt, chart_agent_user_prompt
from dbaide.charts.spec import CHART_TYPES, ChartOptions, ChartSpec, normalize_axis_config, _normalize_step
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient


COMMON_SORT_VALUES = {"value_desc", "value_asc", "category_asc", "none"}


@dataclass(slots=True)
class ChartPlan:
    chart_type: str
    title: str
    category_field: str = ""
    value_fields: list[str] = field(default_factory=list)
    series_names: list[str] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    sort_by: str = "value_desc"
    limit: int = 15
    series_types: list[str] | None = None
    series_axes: list[str] | None = None
    units: list[str] | None = None
    axes: dict[str, dict[str, Any]] | None = None
    options: ChartOptions = field(default_factory=ChartOptions)
    x_field: str = ""
    y_field: str = ""
    size_field: str = ""
    target_field: str = ""
    source_field: str = ""
    path_fields: list[str] = field(default_factory=list)
    open_field: str = ""
    high_field: str = ""
    low_field: str = ""
    close_field: str = ""


class ChartAgent:
    """LLM-backed chart planner; maps tabular rows to a renderer-neutral ChartSpec."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def plan(
        self,
        *,
        question: str,
        intent: str,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> ChartPlan:
        if not rows:
            raise ValueError("no rows to chart")
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("LLM is required for chart planning.")
        return self._llm_plan(question=question, intent=intent, columns=columns, rows=rows)

    def build_spec(self, plan: ChartPlan, *, chart_id: str, rows: list[dict[str, Any]]) -> ChartSpec:
        payload = _materialize(plan, rows)
        spec = ChartSpec(
            chart_id=chart_id,
            chart_type=plan.chart_type,
            title=_clamp_label(plan.title, 40) or "Chart",
            categories=payload.get("categories") or [],
            series=payload.get("series") or [],
            x_label=_clamp_label(plan.x_label, 18),
            y_label=_clamp_label(plan.y_label, 18),
            row_count=len(rows),
            axes={
                key: normalize_axis_config(value)
                for key, value in (plan.axes or {}).items()
                if isinstance(value, dict)
            },
            options=plan.options,
            data=payload.get("data") or {},
        )
        _clamp_display_labels(spec)
        spec.validate()
        return spec

    def render(
        self,
        *,
        chart_id: str,
        question: str,
        intent: str,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> ChartSpec:
        plan = self.plan(question=question, intent=intent, columns=columns, rows=rows)
        return self.build_spec(plan, chart_id=chart_id, rows=rows)

    def _llm_plan(
        self,
        *,
        question: str,
        intent: str,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> ChartPlan:
        payload = self.llm.complete_json(
            [
                LLMMessage("system", chart_agent_system_prompt()),
                LLMMessage(
                    "user",
                    chart_agent_user_prompt(
                        question=question,
                        intent=intent,
                        columns=columns,
                        rows=rows,
                    ),
                ),
            ],
            schema_hint=(
                'Return JSON only: {"chart_type":"bar", "title":"...", '
                '"category_field":"...", "value_fields":["..."], "series_names":["..."], '
                '"series_types":["bar|line|area"], "series_axes":["left|right"], '
                '"x_field":"...", "y_field":"...", "size_field":"...", '
                '"source_field":"...", "target_field":"...", "path_fields":["..."], '
                '"open_field":"...", "high_field":"...", "low_field":"...", "close_field":"...", '
                '"x_label":"...", "y_label":"...", '
                '"axes":{"left":{"label":"...","format":"number|currency|percent","min":0,"max":100,"inverse":false,"log":false},'
                '"right":{"label":"...","format":"number|currency|percent"}}, '
                '"options":{"smooth":false,"step":"none|start|middle|end","show_symbols":true,'
                '"stacked":false,"show_labels":true,"label_mode":"inside|outside|top|center|none",'
                '"area_opacity":0.18,"bar_max_width":42,"donut_inner_ratio":0.56,"rose":false,'
                '"radar_shape":"polygon|circle","node_align":"justify|left|right",'
                '"legend_position":"top|bottom|left|right","gauge_min":0,"gauge_max":100,"gauge_target":80,'
                '"sort_order":"ascending|descending|none"},'
                '"sort_by":"value_desc|value_asc|category_asc|none", "limit":15}'
            ),
        )
        if not isinstance(payload, dict):
            raise ValueError("chart agent returned non-object JSON")

        chart_type = str(payload.get("chart_type") or "").strip()
        if chart_type not in CHART_TYPES:
            raise ValueError(f"chart agent returned unsupported chart_type: {chart_type!r}")

        col_set = set(columns)
        value_fields = [str(v).strip() for v in (payload.get("value_fields") or []) if str(v).strip()]
        category_field = str(payload.get("category_field") or "").strip()
        x_field = str(payload.get("x_field") or "").strip()
        y_field = str(payload.get("y_field") or "").strip()
        size_field = str(payload.get("size_field") or "").strip()
        source_field = str(payload.get("source_field") or "").strip()
        target_field = str(payload.get("target_field") or "").strip()
        path_fields = [str(v).strip() for v in (payload.get("path_fields") or []) if str(v).strip()]
        open_field = str(payload.get("open_field") or "").strip()
        high_field = str(payload.get("high_field") or "").strip()
        low_field = str(payload.get("low_field") or "").strip()
        close_field = str(payload.get("close_field") or "").strip()
        _validate_plan_fields(
            chart_type=chart_type,
            columns=columns,
            category_field=category_field,
            value_fields=value_fields,
            x_field=x_field,
            y_field=y_field,
            size_field=size_field,
            source_field=source_field,
            target_field=target_field,
            path_fields=path_fields,
            open_field=open_field,
            high_field=high_field,
            low_field=low_field,
            close_field=close_field,
        )
        unknown = [f for f in value_fields if f not in col_set]
        if unknown:
            raise ValueError(f"chart agent value_fields not in columns: {unknown!r} (columns: {columns!r})")

        series_names = [str(v) for v in (payload.get("series_names") or []) if str(v).strip()]
        while len(series_names) < len(value_fields):
            series_names.append(value_fields[len(series_names)])
        series_types = _normalize_list(
            payload.get("series_types"),
            len(value_fields),
            default=_default_series_type(chart_type),
            allowed={"bar", "line", "area"},
        )
        series_axes = _normalize_axes_list(
            payload.get("series_axes"),
            len(value_fields),
        )
        units = _string_list(payload.get("units"))
        while len(units) < len(value_fields):
            units.append("")
        raw_axes = payload.get("axes") if isinstance(payload.get("axes"), dict) else {}
        axes_out: dict[str, dict[str, Any]] = {}
        for key in ("left", "right", "x", "y"):
            raw = raw_axes.get(key) if isinstance(raw_axes, dict) else None
            if isinstance(raw, dict):
                axes_out[key] = normalize_axis_config(raw)
        if chart_type in {"combo", "multi_axis_line"} and "right" in series_axes and "right" not in axes_out:
            right_fields = [name for name, axis in zip(series_names, series_axes, strict=False) if axis == "right"]
            axes_out["right"] = {"label": " / ".join(right_fields[:2]), "format": "", "min": None, "max": None, "inverse": False, "log": False}
        if "left" not in axes_out and (value_fields or y_field):
            axes_out["left"] = normalize_axis_config({"label": str(payload.get("y_label") or "").strip(), "format": ""})
        options = chart_options_from_payload(payload.get("options"))
        sort_by = str(payload.get("sort_by") or "value_desc").strip().lower() or "value_desc"
        if sort_by not in COMMON_SORT_VALUES:
            sort_by = "value_desc"
        _sync_funnel_sort_order(chart_type, sort_by, options)
        return ChartPlan(
            chart_type=chart_type,
            title=str(payload.get("title") or intent or "Chart").strip() or "Chart",
            category_field=category_field,
            value_fields=value_fields,
            series_names=series_names[: len(value_fields)],
            x_label=str(payload.get("x_label") or "").strip(),
            y_label=str(payload.get("y_label") or "").strip(),
            sort_by=sort_by,
            limit=_safe_limit(payload.get("limit"), default=15),
            series_types=series_types,
            series_axes=series_axes,
            units=units[: len(value_fields)],
            axes=axes_out,
            options=options,
            x_field=x_field,
            y_field=y_field,
            size_field=size_field,
            source_field=source_field,
            target_field=target_field,
            path_fields=path_fields,
            open_field=open_field,
            high_field=high_field,
            low_field=low_field,
            close_field=close_field,
        )


def chart_options_from_payload(value: Any) -> ChartOptions:
    if not isinstance(value, dict):
        return ChartOptions()
    opts = ChartOptions(
        smooth=value.get("smooth") if isinstance(value.get("smooth"), bool) else None,
        step=_normalize_step(value.get("step")),
        show_symbols=value.get("show_symbols") if isinstance(value.get("show_symbols"), bool) else None,
        stacked=value.get("stacked") if isinstance(value.get("stacked"), bool) else None,
        show_labels=value.get("show_labels") if isinstance(value.get("show_labels"), bool) else None,
        label_mode=str(value.get("label_mode") or "").strip().lower(),
        area_opacity=_safe_ratio(value.get("area_opacity"), default=0.18),
        bar_max_width=_safe_limit(value.get("bar_max_width"), default=42, minimum=8, maximum=72),
        donut_inner_ratio=_safe_ratio(value.get("donut_inner_ratio"), default=0.56, minimum=0.15, maximum=0.9),
        rose=bool(value.get("rose") or False),
        radar_shape=str(value.get("radar_shape") or "").strip().lower(),
        node_align=str(value.get("node_align") or "").strip().lower(),
        legend_position=str(value.get("legend_position") or "").strip().lower(),
        gauge_min=_as_float_or_none(value.get("gauge_min")),
        gauge_max=_as_float_or_none(value.get("gauge_max")),
        gauge_target=_as_float_or_none(value.get("gauge_target")),
        sort_order=str(value.get("sort_order") or "").strip().lower(),
    )
    opts.validate()
    return opts


def _validate_plan_fields(
    *,
    chart_type: str,
    columns: list[str],
    category_field: str,
    value_fields: list[str],
    x_field: str,
    y_field: str,
    size_field: str,
    source_field: str,
    target_field: str,
    path_fields: list[str],
    open_field: str,
    high_field: str,
    low_field: str,
    close_field: str,
) -> None:
    col_set = set(columns)

    def require_field(name: str, value: str) -> None:
        if not value:
            raise ValueError(f"chart agent must return {name}")
        if value not in col_set:
            raise ValueError(f"chart agent {name} {value!r} not in columns: {columns!r}")

    if chart_type in {"bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line", "area", "stacked_area", "multi_axis_line", "combo", "pie", "donut", "funnel", "waterfall", "radar", "boxplot", "candlestick"}:
        require_field("category_field", category_field)
    if chart_type in {"bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line", "area", "stacked_area", "multi_axis_line", "combo", "pie", "donut", "funnel", "waterfall", "radar", "boxplot"}:
        if not value_fields:
            raise ValueError("chart agent must return non-empty value_fields")
    if chart_type in {"scatter", "bubble", "heatmap"}:
        require_field("x_field", x_field or category_field)
    if chart_type in {"scatter", "bubble", "heatmap"}:
        require_field("y_field", y_field)
    if chart_type == "heatmap" and not value_fields:
        raise ValueError("chart agent must return non-empty value_fields")
    if chart_type == "bubble":
        require_field("size_field", size_field)
    if chart_type == "sankey":
        require_field("source_field", source_field)
        require_field("target_field", target_field)
        if not value_fields:
            raise ValueError("chart agent must return non-empty value_fields")
    if chart_type in {"treemap", "sunburst"}:
        if not path_fields:
            raise ValueError("chart agent must return path_fields")
        for field in path_fields:
            require_field("path_fields", field)
        if not value_fields:
            raise ValueError("chart agent must return non-empty value_fields")
    if chart_type == "candlestick":
        require_field("open_field", open_field)
        require_field("high_field", high_field)
        require_field("low_field", low_field)
        require_field("close_field", close_field)
    if chart_type == "gauge" and not value_fields:
        raise ValueError("chart agent must return non-empty value_fields")


def _materialize(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if plan.chart_type in {"scatter", "bubble", "heatmap", "sankey", "treemap", "sunburst", "candlestick", "boxplot", "gauge", "radar"}:
        return _materialize_special(plan, rows)
    categories, series = _materialize_common(plan, rows)
    return {"categories": categories, "series": series, "data": {}}


def _materialize_common(plan: ChartPlan, rows: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    pairs: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        label = str(row.get(plan.category_field, "") or "").strip() or "—"
        pairs.append((label, row))
    if plan.chart_type in ("pie", "donut", "funnel", "waterfall") and len(plan.value_fields) != 1:
        plan = _truncate_plan_fields(plan, keep=1)
    if plan.sort_by == "value_desc" and plan.value_fields:
        key = plan.value_fields[0]
        pairs.sort(key=lambda item: _as_float(item[1].get(key)), reverse=True)
    elif plan.sort_by == "value_asc" and plan.value_fields:
        key = plan.value_fields[0]
        pairs.sort(key=lambda item: _as_float(item[1].get(key)))
    elif plan.sort_by == "category_asc":
        pairs.sort(key=lambda item: item[0])
    pairs = pairs[: plan.limit]

    categories = [label for label, _ in pairs]
    series: list[dict[str, Any]] = []
    for idx, field in enumerate(plan.value_fields):
        name = plan.series_names[idx] if idx < len(plan.series_names) else field
        values = [_as_float(row.get(field)) for _, row in pairs]
        item = {"name": name, "values": values, "field": field}
        if plan.series_types and idx < len(plan.series_types):
            item["type"] = plan.series_types[idx]
        if plan.series_axes and idx < len(plan.series_axes):
            item["axis"] = plan.series_axes[idx]
        if plan.units and idx < len(plan.units) and plan.units[idx]:
            item["unit"] = plan.units[idx]
        series.append(item)
    return categories, series


def _materialize_special(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if plan.chart_type == "scatter":
        return _materialize_scatter_like(plan, rows, bubble=False)
    if plan.chart_type == "bubble":
        return _materialize_scatter_like(plan, rows, bubble=True)
    if plan.chart_type == "heatmap":
        return _materialize_heatmap(plan, rows)
    if plan.chart_type == "sankey":
        return _materialize_sankey(plan, rows)
    if plan.chart_type in {"treemap", "sunburst"}:
        return _materialize_tree(plan, rows)
    if plan.chart_type == "candlestick":
        return _materialize_candlestick(plan, rows)
    if plan.chart_type == "boxplot":
        return _materialize_boxplot(plan, rows)
    if plan.chart_type == "gauge":
        return _materialize_gauge(plan, rows)
    if plan.chart_type == "radar":
        return _materialize_radar(plan, rows)
    raise ValueError(f"unsupported special chart_type: {plan.chart_type}")


def _materialize_scatter_like(plan: ChartPlan, rows: list[dict[str, Any]], *, bubble: bool) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    x_key = plan.x_field or plan.category_field
    for row in rows[: plan.limit]:
        entry = {
            "name": str(row.get(plan.category_field) or row.get(x_key) or "—"),
            "x": _as_float(row.get(x_key)),
            "y": _as_float(row.get(plan.y_field)),
        }
        if bubble:
            entry["size"] = max(2.0, _as_float(row.get(plan.size_field)))
        points.append(entry)
    return {
        "categories": [],
        "series": [{
            "name": plan.series_names[0] if plan.series_names else (plan.y_field or "value"),
            "values": [p["y"] for p in points],
        }],
        "data": {"points": points},
    }


def _materialize_heatmap(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_vals: list[str] = []
    y_vals: list[str] = []
    x_key = plan.x_field or plan.category_field
    value_field = plan.value_fields[0]
    cell_totals: dict[tuple[str, str], float] = {}
    for row in rows[: plan.limit]:
        xv = str(row.get(x_key) or "—")
        yv = str(row.get(plan.y_field) or "—")
        key = (xv, yv)
        cell_totals[key] = cell_totals.get(key, 0.0) + _as_float(row.get(value_field))
    for xv, yv in cell_totals:
        if xv not in x_vals:
            x_vals.append(xv)
        if yv not in y_vals:
            y_vals.append(yv)
    points: list[list[float | int]] = [
        [x_vals.index(xv), y_vals.index(yv), total]
        for (xv, yv), total in cell_totals.items()
    ]
    return {"categories": [], "series": [], "data": {"x_categories": x_vals, "y_categories": y_vals, "points": points}}


def _materialize_sankey(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    value_field = plan.value_fields[0]
    nodes: dict[str, dict[str, Any]] = {}
    link_totals: dict[tuple[str, str], float] = {}
    for row in rows[: plan.limit]:
        source = str(row.get(plan.source_field) or "—")
        target = str(row.get(plan.target_field) or "—")
        nodes.setdefault(source, {"name": source})
        nodes.setdefault(target, {"name": target})
        key = (source, target)
        link_totals[key] = link_totals.get(key, 0.0) + _as_float(row.get(value_field))
    links = [
        {"source": source, "target": target, "value": value}
        for (source, target), value in link_totals.items()
    ]
    return {"categories": [], "series": [], "data": {"nodes": list(nodes.values()), "links": links}}


def _materialize_tree(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    value_field = plan.value_fields[0]
    root: dict[str, Any] = {"name": plan.title or "root", "children": []}
    for row in rows[: plan.limit]:
        pointer = root
        for field in plan.path_fields:
            label = str(row.get(field) or "—")
            children = pointer.setdefault("children", [])
            child = next((c for c in children if c.get("name") == label), None)
            if child is None:
                child = {"name": label, "children": []}
                children.append(child)
            pointer = child
        value = _as_float(row.get(value_field))
        pointer["value"] = _as_float(pointer.get("value")) + value
        if not pointer.get("children"):
            pointer.pop("children", None)
    return {"categories": [], "series": [], "data": {"tree": root.get("children") or []}}


def _materialize_candlestick(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = rows[: plan.limit]
    categories = [str(row.get(plan.category_field) or "—") for row in items]
    ohlc = [
        [
            _as_float(row.get(plan.open_field)),
            _as_float(row.get(plan.close_field)),
            _as_float(row.get(plan.low_field)),
            _as_float(row.get(plan.high_field)),
        ]
        for row in items
    ]
    return {"categories": categories, "series": [], "data": {"ohlc": ohlc}}


def _materialize_boxplot(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    field = plan.value_fields[0]
    for row in rows:
        key = str(row.get(plan.category_field) or "—")
        groups.setdefault(key, []).append(_as_float(row.get(field)))
    items = list(groups.items())[: plan.limit]
    categories = [name for name, _ in items]
    boxes: list[list[float]] = []
    for _name, values in items:
        clean = sorted(v for v in values if math.isfinite(v))
        if not clean:
            boxes.append([0.0, 0.0, 0.0, 0.0, 0.0])
            continue
        boxes.append([
            clean[0],
            _percentile(clean, 25),
            _percentile(clean, 50),
            _percentile(clean, 75),
            clean[-1],
        ])
    return {"categories": categories, "series": [], "data": {"boxes": boxes, "outliers": []}}


def _materialize_gauge(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    field = plan.value_fields[0]
    row = rows[0]
    label = str(
        row.get(plan.category_field)
        or (plan.series_names[0] if plan.series_names else field)
    )
    value = _as_float(row.get(field))
    return {"categories": [], "series": [], "data": {"value": value, "name": label}}


def _materialize_radar(plan: ChartPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    indicators = []
    for field, name in zip(plan.value_fields, plan.series_names or plan.value_fields, strict=False):
        peak = max((_as_float(row.get(field)) for row in rows), default=0.0)
        indicators.append({"name": name, "max": max(1.0, peak * 1.1)})
    radar_series = []
    for row in rows[: plan.limit]:
        radar_series.append({
            "name": str(row.get(plan.category_field) or "—"),
            "value": [_as_float(row.get(field)) for field in plan.value_fields],
        })
    return {"categories": [], "series": [], "data": {"indicators": indicators, "radar_series": radar_series}}


def _truncate_plan_fields(plan: ChartPlan, *, keep: int) -> ChartPlan:
    return ChartPlan(
        chart_type=plan.chart_type,
        title=plan.title,
        category_field=plan.category_field,
        value_fields=plan.value_fields[:keep],
        series_names=plan.series_names[:keep],
        x_label=plan.x_label,
        y_label=plan.y_label,
        sort_by=plan.sort_by,
        limit=plan.limit,
        series_types=(plan.series_types or [])[:keep],
        series_axes=(plan.series_axes or [])[:keep],
        units=(plan.units or [])[:keep],
        axes=plan.axes,
        options=plan.options,
        x_field=plan.x_field,
        y_field=plan.y_field,
        size_field=plan.size_field,
        target_field=plan.target_field,
        source_field=plan.source_field,
        path_fields=list(plan.path_fields),
        open_field=plan.open_field,
        high_field=plan.high_field,
        low_field=plan.low_field,
        close_field=plan.close_field,
    )


def _clamp_display_labels(spec: ChartSpec) -> None:
    spec.title = _clamp_label(spec.title, 40)
    spec.x_label = _clamp_label(spec.x_label, 18)
    spec.y_label = _clamp_label(spec.y_label, 18)
    for axis in spec.axes.values():
        if "label" in axis:
            axis["label"] = _clamp_label(axis.get("label", ""), 18)
    for s in spec.series:
        s["name"] = _clamp_label(s.get("name", ""), 16)
    if spec.chart_type in {"radar"}:
        indicators = spec.data.get("indicators") or []
        for indicator in indicators:
            if isinstance(indicator, dict):
                indicator["name"] = _clamp_label(indicator.get("name", ""), 16)


def _clamp_label(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(1, limit - 1)].rstrip() + "…"


def _default_series_type(chart_type: str) -> str:
    if chart_type in {"line", "area", "multi_axis_line"}:
        return "line"
    if chart_type == "stacked_area":
        return "area"
    return "bar"


def _normalize_axes_list(value: Any, length: int) -> list[str]:
    """Map LLM axis hints onto per-series left/right assignments."""
    allowed = {"left", "right"}
    items = _string_list(value)
    if len(items) == 1 and length == 2:
        single = items[0].strip().lower()
        if single == "right":
            return ["left", "right"]
        if single in allowed:
            return [single, single]
    if len(items) == 1 and length > 2:
        single = items[0].strip().lower()
        if single == "right":
            return ["left"] + ["right"] * (length - 1)
    return _normalize_list(value, length, default="left", allowed=allowed)


def _normalize_list(value: Any, length: int, *, default: str, allowed: set[str]) -> list[str]:
    items = _string_list(value)
    if len(items) == 1 and length > 1:
        items = items * length
    out: list[str] = []
    for item in items[:length]:
        item = item.strip().lower()
        out.append(item if item in allowed else default)
    while len(out) < length:
        out.append(default)
    return out


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list | tuple):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _safe_limit(value: Any, *, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_ratio(value: Any, *, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _sync_funnel_sort_order(chart_type: str, sort_by: str, options: ChartOptions) -> None:
    if chart_type != "funnel" or options.sort_order:
        return
    mapping = {
        "value_desc": "descending",
        "value_asc": "ascending",
        "category_asc": "none",
        "none": "none",
    }
    options.sort_order = mapping.get(sort_by, "descending")


def _step_value(value: Any) -> str:
    return _normalize_step(value)


def _as_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _as_float(value)


def _as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    try:
        out = float(value)
        return out if math.isfinite(out) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    pos = (len(values) - 1) * (q / 100.0)
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return values[lower]
    weight = pos - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight
