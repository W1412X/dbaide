"""Conversational dashboard-builder agent.

SEPARATE from ChartAgent (rows→chart) and the Ask orchestrator (Q&A). It authors
a DECLARATIVE layout (rows of typed tiles) + the named parameterized recipes —
never HTML. The system renders the layout (render_body); the result is a
:class:`ParametricDashboard` whose recipes the runtime executes via the bridge.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.agent.prompts.dashboard_builder import (
    dashboard_builder_system_prompt,
    dashboard_builder_user_prompt,
)
from dbaide.boards.parametric import ParametricChart, ParametricDashboard
from dbaide.boards.runtime import render_sql
from dbaide.rendering.dashboard_body import render_body
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

Validate = Callable[[str], Any]


class DashboardBuilderAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    MAX_REPAIR_ROUNDS = 2

    def build(
        self,
        *,
        instruction: str,
        context_charts: list[dict[str, Any]] | None = None,
        connection_name: str = "",
        schema_context: str = "",
        existing: ParametricDashboard | None = None,
        validate: Validate | None = None,
        dialect: str = "",
    ) -> ParametricDashboard:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("An LLM is required to build a dashboard.")
        existing_payload = None
        if existing is not None:
            existing_payload = {"name": existing.name, "ui": existing.layout,
                                "charts": [c.to_dict() for c in existing.charts]}
        messages = [
            LLMMessage("system", dashboard_builder_system_prompt()),
            LLMMessage("user", dashboard_builder_user_prompt(
                instruction=instruction,
                context_charts=list(context_charts or []),
                schema_context=schema_context,
                existing=existing_payload,
                dialect=dialect,
            )),
        ]
        payload: dict[str, Any] = {}
        charts: list[ParametricChart] = []
        errors: list[str] = []
        # Generate, then VALIDATE each recipe against the real database (the validate
        # callback EXPLAINs it). Static checks miss invented columns / unsupported
        # functions, so we feed any DB error back to the model and let it self-correct.
        for _round in range(self.MAX_REPAIR_ROUNDS + 1):
            payload = self.llm.complete_json(messages)
            if not isinstance(payload, dict):
                raise ValueError("builder returned a non-object result")
            charts = [ParametricChart.from_dict(c) for c in (payload.get("charts") or []) if isinstance(c, dict)]
            if not charts:
                raise ValueError("builder returned no charts")
            errors = self._validation_errors(charts, validate) if validate is not None else []
            if not errors:
                break
            if _round < self.MAX_REPAIR_ROUNDS:
                messages.append(LLMMessage("assistant", json.dumps(payload, ensure_ascii=False)))
                messages.append(LLMMessage("user", self._repair_prompt(errors, dialect)))
        if errors:
            raise ValueError("recipes still fail against the database after repair attempts: "
                             + " | ".join(errors[:4]))

        # The agent emits a declarative COMPONENT TREE (ui), NOT HTML. The system
        # renders it deterministically (render_body), so generation quality can never
        # break or uglify the page — a malformed/missing tree falls back to an auto-grid.
        raw = payload.get("ui")
        if raw is None:
            raw = payload.get("layout")
        if raw is None:
            raw = payload.get("rows")
        if isinstance(raw, dict):
            layout: Any = raw                                   # component tree
        elif isinstance(raw, list):
            layout = [r for r in raw if isinstance(r, dict)]    # list of nodes / legacy rows
        else:
            layout = []

        app = existing or ParametricDashboard(name="", connection_name=connection_name)
        app.name = str(payload.get("name") or app.name or "交互看板")
        app.connection_name = connection_name or app.connection_name
        app.charts = charts
        app.layout = layout
        app.html = render_body(layout, charts)
        return app

    @staticmethod
    def _repair_prompt(errors: list[str], dialect: str) -> str:
        return (
            "These recipe SQLs FAILED when run against the database:\n"
            + "\n".join(f"- {e}" for e in errors[:10])
            + f"\n\nFix them. Use ONLY columns and functions that exist in the schema above; the "
              f"engine is {dialect or 'SQLite'} — no array/dialect-specific functions, and no "
              "optional-filter logic (`:p IS NULL OR ...`), just a simple predicate per filter. "
              "Return the COMPLETE corrected dashboard JSON (same shape)."
        )

    @staticmethod
    def _validation_errors(charts: list[ParametricChart], validate: Validate) -> list[str]:
        errors: list[str] = []
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
                    errors.append(f"chart {chart.chart_id!r}: " + "; ".join(map(str, issues or [])))
        return errors
