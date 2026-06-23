"""Internal task-list tool for the Ask loop."""

from __future__ import annotations

from typing import Any

from dbaide.agent.agenda import agenda_from_dict, agenda_summary, agenda_to_dict
from dbaide.agent.toolkit.support import _err
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import UPDATE_AGENDA


def register(registry: ToolRegistry, orchestrator) -> None:
    def _update_agenda(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        items = args.get("items")
        if not isinstance(items, list):
            return ToolResult(ok=False, error=_err("update_agenda", "items must be a list"))
        agenda = agenda_from_dict(items, previous=orchestrator.run_state.agenda)
        orchestrator.run_state.agenda = agenda
        explanation = " ".join(str(args.get("explanation") or "").split()).strip()
        agenda_payload = {
            "items": agenda_to_dict(agenda),
            "explanation": explanation,
        }
        data = {
            "updated": True,
            "summary": agenda_summary(agenda),
            "agenda": agenda_payload,
        }
        # Carry the structured agenda into the trace event's metadata so it survives
        # persistence/reload (the generic output_preview is a truncated string); the
        # conversation's agenda panel rebuilds from this.
        return ToolResult(ok=True, data=data, meta={"agenda": agenda_payload})

    registry.register(UPDATE_AGENDA, _update_agenda)
