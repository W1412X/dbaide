"""Dedicated chart-planning agent (separate from the main Ask loop)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.agent.prompts.chart_agent import chart_agent_system_prompt, chart_agent_user_prompt
from dbaide.charts.spec import ChartSpec
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient


CHART_TYPES = frozenset({
    "bar", "horizontal_bar", "line", "area", "pie", "donut", "stacked_bar", "scatter",
    "combo", "grouped_bar", "stacked_area", "multi_axis_line",
})


@dataclass(slots=True)
class ChartPlan:
    chart_type: str
    title: str
    category_field: str
    value_fields: list[str]
    series_names: list[str]
    x_label: str
    y_label: str
    sort_by: str = "value_desc"
    limit: int = 15
    series_types: list[str] | None = None
    series_axes: list[str] | None = None
    units: list[str] | None = None
    axes: dict[str, dict[str, Any]] | None = None


class ChartAgent:
    """LLM-backed chart planner; maps tabular rows → ChartSpec for Qt Charts."""

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
        categories, series = _materialize(plan, rows)
        # Clamp model-authored labels deterministically here rather than asking the
        # model to count characters in the prompt — same display result, no format
        # burden on the model (it just writes a meaningful label).
        for s in series:
            s["name"] = _clamp_label(s.get("name", ""), 16)
        axes = {k: dict(v) for k, v in (plan.axes or {}).items()}
        for ax in axes.values():
            if "label" in ax:
                ax["label"] = _clamp_label(ax.get("label", ""), 18)
        spec = ChartSpec(
            chart_id=chart_id,
            chart_type=plan.chart_type,
            title=_clamp_label(plan.title, 40) or "Chart",
            categories=categories,
            series=series,
            x_label=_clamp_label(plan.x_label, 18),
            y_label=_clamp_label(plan.y_label, 18),
            row_count=len(rows),
            axes=axes,
        )
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
                '"x_label":"...", "y_label":"...", "axes":{"left":{"label":"..."},'
                '"right":{"label":"..."}}, "sort_by":"value_desc", "limit":15}'
            ),
        )
        if not isinstance(payload, dict):
            raise ValueError("chart agent returned non-object JSON")
        chart_type = str(payload.get("chart_type") or "").strip()
        if chart_type not in CHART_TYPES:
            raise ValueError(f"chart agent returned unsupported chart_type: {chart_type!r}")
        value_fields = [str(v).strip() for v in (payload.get("value_fields") or []) if str(v).strip()]
        if not value_fields:
            raise ValueError("chart agent must return non-empty value_fields")
        category_field = str(payload.get("category_field") or "").strip()
        if not category_field:
            raise ValueError("chart agent must return category_field")
        col_set = set(columns)
        if category_field not in col_set:
            raise ValueError(
                f"chart agent category_field {category_field!r} not in columns: {columns!r}"
            )
        unknown = [f for f in value_fields if f not in col_set]
        if unknown:
            raise ValueError(
                f"chart agent value_fields not in columns: {unknown!r} (columns: {columns!r})"
            )
        series_names = [str(v) for v in (payload.get("series_names") or []) if str(v).strip()]
        while len(series_names) < len(value_fields):
            series_names.append(value_fields[len(series_names)])
        series_types = _normalize_list(
            payload.get("series_types"),
            len(value_fields),
            default=_default_series_type(chart_type),
            allowed={"bar", "line", "area"},
        )
        series_axes = _normalize_list(
            payload.get("series_axes"),
            len(value_fields),
            default="left",
            allowed={"left", "right"},
        )
        units = _string_list(payload.get("units"))
        while len(units) < len(value_fields):
            units.append("")
        axes = payload.get("axes") if isinstance(payload.get("axes"), dict) else {}
        axes_out: dict[str, dict[str, Any]] = {}
        for key in ("left", "right"):
            raw = axes.get(key) if isinstance(axes, dict) else None
            if isinstance(raw, dict):
                axes_out[key] = {
                    "label": str(raw.get("label") or "").strip(),
                    "format": str(raw.get("format") or "").strip(),
                }
        if chart_type in {"combo", "multi_axis_line"} and "right" in series_axes and "right" not in axes_out:
            right_fields = [
                name for name, axis in zip(series_names, series_axes, strict=False)
                if axis == "right"
            ]
            axes_out["right"] = {"label": " / ".join(right_fields[:2]), "format": ""}
        if "left" not in axes_out:
            axes_out["left"] = {"label": str(payload.get("y_label") or "").strip(), "format": ""}
        return ChartPlan(
            chart_type=chart_type,
            title=str(payload.get("title") or intent or "Chart").strip() or "Chart",
            category_field=category_field,
            value_fields=value_fields,
            series_names=series_names[: len(value_fields)],
            x_label=str(payload.get("x_label") or "").strip(),
            y_label=str(payload.get("y_label") or "").strip(),
            sort_by=str(payload.get("sort_by") or "value_desc").strip() or "value_desc",
            limit=_safe_limit(payload.get("limit"), default=15),
            series_types=series_types,
            series_axes=series_axes,
            units=units[: len(value_fields)],
            axes=axes_out,
        )


def _materialize(plan: ChartPlan, rows: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    pairs: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        label = str(row.get(plan.category_field, "") or "").strip() or "—"
        pairs.append((label, row))
    if plan.chart_type in ("pie", "donut") and len(plan.value_fields) != 1:
        plan = ChartPlan(
            chart_type=plan.chart_type,
            title=plan.title,
            category_field=plan.category_field,
            value_fields=plan.value_fields[:1],
            series_names=plan.series_names[:1],
            x_label=plan.x_label,
            y_label=plan.y_label,
            sort_by=plan.sort_by,
            limit=plan.limit,
            series_types=(plan.series_types or [])[:1],
            series_axes=(plan.series_axes or [])[:1],
            units=(plan.units or [])[:1],
            axes=plan.axes,
        )
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


def _clamp_label(text: Any, limit: int) -> str:
    """Collapse whitespace and truncate a display label to *limit* chars with an
    ellipsis. Deterministic backstop so the model needn't police label length."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(1, limit - 1)].rstrip() + "…"


def _default_series_type(chart_type: str) -> str:
    if chart_type in {"line", "area", "multi_axis_line"}:
        return "line"
    if chart_type == "stacked_area":
        return "area"
    return "bar"


def _normalize_list(value: Any, length: int, *, default: str, allowed: set[str]) -> list[str]:
    items = _string_list(value)
    if len(items) == 1 and length > 1:
        items = items * length
    out: list[str] = []
    for item in items[:length]:
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


def _safe_limit(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(100, parsed))


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
