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
    limit: int = 20


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
        spec = ChartSpec(
            chart_id=chart_id,
            chart_type=plan.chart_type,
            title=plan.title,
            categories=categories,
            series=series,
            x_label=plan.x_label,
            y_label=plan.y_label,
            row_count=len(rows),
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
                '"x_label":"...", "y_label":"...", "sort_by":"value_desc", "limit":20}'
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
        return ChartPlan(
            chart_type=chart_type,
            title=str(payload.get("title") or intent or "Chart").strip() or "Chart",
            category_field=category_field,
            value_fields=value_fields,
            series_names=series_names[: len(value_fields)],
            x_label=str(payload.get("x_label") or "").strip(),
            y_label=str(payload.get("y_label") or "").strip(),
            sort_by=str(payload.get("sort_by") or "value_desc").strip() or "value_desc",
            limit=max(1, min(100, int(payload.get("limit") or 20))),
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
        series.append({"name": name, "values": values, "field": field})
    return categories, series


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
