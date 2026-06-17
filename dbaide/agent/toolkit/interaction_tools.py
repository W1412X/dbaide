"""User-interaction tool (ask_user)."""
from __future__ import annotations

from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import ASK_USER
from dbaide.agent.toolkit.support import _err, _string_list


def register(registry: ToolRegistry, orchestrator) -> None:
    def _ask_user(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("ask_user", "question is required"))
        raw_value = args.get("options")
        raw_options = raw_value if isinstance(raw_value, list) else []
        if isinstance(raw_value, list):
            options = [_option_label(item) for item in raw_options]
            options = [item for item in options if item]
        else:
            options = _string_list(raw_value)
        orchestrator.run_state.pending_question = question
        orchestrator.run_state.pending_options = options
        orchestrator.run_state.pending_questions = [{"ask": question, "options": options}]
        return ToolResult(
            ok=True,
            data={
                "pending": True,
                "question": question,
                "options": options,
                "raw_options": raw_options,
            },
        )

    registry.register(ASK_USER, _ask_user)


def _option_label(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("label", "text", "title", "value"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(item or "").strip()
