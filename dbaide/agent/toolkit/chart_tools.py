"""render_chart tool: delegate chart planning to ChartAgent and store renderer-neutral specs."""

from __future__ import annotations

from typing import Any

from dbaide.agent.chart_agent import ChartAgent
from dbaide.agent.progress_events import subagent_event
from dbaide.charts.data import resolve_chart_rows
from dbaide.charts.embed import chart_embed_markdown
from dbaide.charts.spec import chart_spec_to_dict
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import RENDER_CHART
from dbaide.agent.toolkit.support import _err


def register(registry: ToolRegistry, orchestrator) -> None:
    def _render_chart(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        artifact_id = str(args.get("artifact_id") or "").strip()
        intent = str(args.get("intent") or "").strip()
        inline = args.get("data") if isinstance(args.get("data"), list) else None
        rows, columns = resolve_chart_rows(
            orchestrator,
            artifact_id=artifact_id,
            data=inline,
        )
        if not rows:
            return ToolResult(
                ok=False,
                error=_err(
                    "render_chart",
                    "no tabular data available; run execute_sql first or pass data",
                    retryable=True,
                ),
            )
        if not columns:
            columns = list(rows[0].keys())

        parent = orchestrator.run_state.trace_node or "render_chart"
        orchestrator.progress(subagent_event(
            agent="chart_agent",
            parent_id=parent,
            title="Planning chart",
            status="running",
            detail=f"{len(rows)} rows · {len(columns)} cols",
        ))

        chart_id = _next_chart_id(orchestrator.run_state)
        agent = ChartAgent(orchestrator.llm)
        try:
            spec = agent.render(
                chart_id=chart_id,
                question=str(orchestrator.run_state.question or ""),
                intent=intent,
                columns=columns,
                rows=rows,
            )
        except Exception as exc:
            orchestrator.progress(subagent_event(
                agent="chart_agent",
                parent_id=parent,
                title="Chart planning failed",
                status="failed",
                detail=str(exc)[:200],
            ))
            return ToolResult(ok=False, error=_err("render_chart", str(exc), retryable=True))

        payload = chart_spec_to_dict(spec)
        charts = list(getattr(orchestrator.run_state, "charts", []) or [])
        charts.append(payload)
        orchestrator.run_state.charts = charts

        orchestrator.progress(subagent_event(
            agent="chart_agent",
            parent_id=parent,
            title=f"Chart ready · {spec.chart_type}",
            status="completed",
            detail=spec.title[:160],
        ))
        return ToolResult(
            ok=True,
            data={
                "chart_id": spec.chart_id,
                "chart_type": spec.chart_type,
                "title": spec.title,
                "row_count": spec.row_count,
                "preview": f"{spec.title} ({spec.row_count} pts)",
                "embed_markdown": chart_embed_markdown(spec.chart_id),
            },
        )

    registry.register(RENDER_CHART, _render_chart)


def _next_chart_id(run_state: Any) -> str:
    existing = {
        str(item.get("chart_id") or "")
        for item in (getattr(run_state, "charts", []) or [])
        if isinstance(item, dict)
    }
    n = 1
    while True:
        chart_id = f"chart:{n}"
        if chart_id not in existing:
            return chart_id
        n += 1
