"""Conversational dashboard-builder agent.

SEPARATE from ChartAgent (rows→chart) and the Ask orchestrator (Q&A). It authors
an interactive HTML dashboard + the named parameterized recipes behind it, and
refines them across turns. Output is a :class:`ParametricDashboard` (with HTML);
the runtime executes the recipes and the WebChannel bridge serves the page.
"""

from __future__ import annotations

from typing import Any, Callable

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.agent.prompts.dashboard_builder import (
    dashboard_builder_system_prompt,
    dashboard_builder_user_prompt,
)
from dbaide.boards.parametric import ParametricChart, ParametricDashboard
from dbaide.boards.runtime import render_sql
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

Validate = Callable[[str], Any]


class DashboardBuilderAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def build(
        self,
        *,
        instruction: str,
        context_charts: list[dict[str, Any]] | None = None,
        connection_name: str = "",
        schema_context: str = "",
        existing: ParametricDashboard | None = None,
        validate: Validate | None = None,
    ) -> ParametricDashboard:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("An LLM is required to build a dashboard.")
        existing_payload = None
        if existing is not None:
            existing_payload = {"name": existing.name, "html": existing.html,
                                "charts": [c.to_dict() for c in existing.charts]}
        payload = self.llm.complete_json([
            LLMMessage("system", dashboard_builder_system_prompt()),
            LLMMessage("user", dashboard_builder_user_prompt(
                instruction=instruction,
                context_charts=list(context_charts or []),
                schema_context=schema_context,
                existing=existing_payload,
            )),
        ])
        if not isinstance(payload, dict):
            raise ValueError("builder returned a non-object result")
        charts = [ParametricChart.from_dict(c) for c in (payload.get("charts") or []) if isinstance(c, dict)]
        if not charts:
            raise ValueError("builder returned no charts")
        html = str(payload.get("html") or "")
        if not html.strip():
            raise ValueError("builder returned no HTML")
        if validate is not None:
            self._validate(charts, validate)

        app = existing or ParametricDashboard(name="", connection_name=connection_name)
        app.name = str(payload.get("name") or app.name or "交互看板")
        app.connection_name = connection_name or app.connection_name
        app.charts = charts
        app.html = html
        return app

    @staticmethod
    def _validate(charts: list[ParametricChart], validate: Validate) -> None:
        for chart in charts:
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
                        f"chart {chart.chart_id!r} source failed validation: " + "; ".join(map(str, issues or [])))
