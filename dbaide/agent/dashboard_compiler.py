"""Dashboard-compiler agent — turns a chart's fixed SQL into a parameterized recipe.

DELIBERATELY SEPARATE from:
- ``ChartAgent`` (which maps result rows → a chart spec), and
- the Ask orchestrator / loop (which answers questions conversationally).

It runs once, with its own prompt and its own model call, and emits a
:class:`dbaide.boards.parametric.ParametricChart`. The chart's *shape* is reused
from the existing chart plan — this agent only parameterizes the query and
designs the controls. The runtime then executes the recipe with no model call.
"""

from __future__ import annotations

from typing import Any, Callable

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.agent.prompts.dashboard_compiler import (
    dashboard_compiler_system_prompt,
    dashboard_compiler_user_prompt,
)
from dbaide.boards.parametric import ParametricChart
from dbaide.boards.runtime import render_sql
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

_PLAN_FIELD_KEYS = ("category_field", "value_fields", "series_names", "x_field", "y_field", "path_fields")

# validate(sql) -> report with `.ok` (bool) and `.issues` (list); a dict works too.
Validate = Callable[[str], Any]


class DashboardCompiler:
    """LLM-backed compiler from (sql + chart plan) → re-runnable parametric recipe."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def compile_chart(
        self,
        *,
        chart_id: str,
        title: str,
        source_sql: str,
        chart_plan: dict[str, Any],
        nl_question: str = "",
        schema_context: str = "",
        validate: Validate | None = None,
    ) -> ParametricChart:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("An LLM is required to compile a parameterized dashboard.")
        plan = dict(chart_plan or {})
        plan_fields = {k: plan[k] for k in _PLAN_FIELD_KEYS if plan.get(k)}
        payload = self.llm.complete_json([
            LLMMessage("system", dashboard_compiler_system_prompt()),
            LLMMessage("user", dashboard_compiler_user_prompt(
                nl_question=nl_question,
                source_sql=source_sql,
                chart_type=str(plan.get("chart_type") or "bar"),
                plan_fields=plan_fields,
                schema_context=schema_context,
            )),
        ])
        if not isinstance(payload, dict):
            raise ValueError("compiler returned a non-object recipe")

        chart = ParametricChart.from_dict({
            "sources": payload.get("sources"),
            "params": payload.get("params"),
            "combine": payload.get("combine"),
            "chart_id": chart_id,
            "title": title,
            "chart_plan": plan,     # reuse the existing chart shape — not re-derived
        })
        if not chart.sources:
            raise ValueError("compiler returned no SQL sources")
        if validate is not None:
            self._validate(chart, validate)
        return chart

    @staticmethod
    def _validate(chart: ParametricChart, validate: Validate) -> None:
        """Bind defaults and run each source template through the read-only validator."""
        values = chart.default_params()
        for src in chart.sources:
            bound = render_sql(src.sql, values, chart.params)
            report = validate(bound)
            ok = getattr(report, "ok", None)
            if ok is None and isinstance(report, dict):
                ok = report.get("ok")
            if ok is False:
                issues = getattr(report, "issues", None)
                if issues is None and isinstance(report, dict):
                    issues = report.get("issues")
                raise ValueError(
                    f"compiled source {src.id!r} failed validation: " + "; ".join(map(str, issues or []))
                )
