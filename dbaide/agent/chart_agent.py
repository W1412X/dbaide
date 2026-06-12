"""Dedicated chart-planning agent (separate from the main Ask loop)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from dbaide.agent.prompts.chart_agent import chart_agent_system_prompt, chart_agent_user_prompt
from dbaide.charts.spec import ChartSpec
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

logger = logging.getLogger("dbaide.chart_agent")

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
        if not isinstance(self.llm, NullLLMClient):
            try:
                return self._llm_plan(question=question, intent=intent, columns=columns, rows=rows)
            except Exception as exc:
                logger.warning("chart agent LLM failed, using heuristic fallback: %s", exc)
        return _heuristic_plan(columns, rows, intent=intent)

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
        chart_type = str(payload.get("chart_type") or "bar").strip()
        if chart_type not in CHART_TYPES:
            chart_type = "bar"
        value_fields = [str(v) for v in (payload.get("value_fields") or []) if str(v).strip()]
        series_names = [str(v) for v in (payload.get("series_names") or []) if str(v).strip()]
        category_field = str(payload.get("category_field") or "").strip()
        if not value_fields:
            value_fields, category_field = _infer_fields(columns, rows, category_field)
        if not category_field:
            category_field, _ = _infer_fields(columns, rows, "")
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


def _heuristic_plan(columns: list[str], rows: list[dict[str, Any]], *, intent: str = "") -> ChartPlan:
    category_field, value_fields = _infer_fields(columns, rows, "")
    chart_type = "horizontal_bar"
    if len(value_fields) > 1:
        chart_type = "stacked_bar"
    if len(rows) <= 8 and len(value_fields) == 1:
        chart_type = "pie"
    return ChartPlan(
        chart_type=chart_type,
        title=intent.strip() or "Chart",
        category_field=category_field,
        value_fields=value_fields[:3],
        series_names=value_fields[:3],
        x_label=value_fields[0] if value_fields else "",
        y_label=category_field,
        sort_by="value_desc",
        limit=20,
    )


def _infer_fields(
    columns: list[str],
    rows: list[dict[str, Any]],
    preferred_category: str,
) -> tuple[str, list[str]]:
    numeric: list[str] = []
    text: list[str] = []
    for col in columns:
        if _column_is_numeric(rows, col):
            numeric.append(col)
        else:
            text.append(col)
    category = preferred_category if preferred_category in columns else ""
    if not category:
        category = text[0] if text else (columns[0] if columns else "")
    if not numeric:
        numeric = [c for c in columns if c != category]
    return category, numeric


def _column_is_numeric(rows: list[dict[str, Any]], column: str) -> bool:
    seen = 0
    numeric = 0
    for row in rows[:40]:
        val = row.get(column)
        if val is None or val == "":
            continue
        seen += 1
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            numeric += 1
        else:
            try:
                float(val)
                numeric += 1
            except (TypeError, ValueError):
                return False
    return seen > 0 and numeric == seen


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
