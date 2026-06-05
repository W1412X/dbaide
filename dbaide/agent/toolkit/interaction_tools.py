"""User-interaction tool (ask_user)."""
from __future__ import annotations

from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import ASK_USER
from dbaide.agent.toolkit.support import _err


def register(registry: ToolRegistry, orchestrator) -> None:
    def _ask_user(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        question = str(args.get("question") or "").strip()
        if not question:
            return ToolResult(ok=False, error=_err("ask_user", "question is required"))
        options_raw = args.get("options")
        options: list[str] = []
        if isinstance(options_raw, list):
            options = [str(item).strip() for item in options_raw if str(item).strip()]
        orchestrator.run_state.pending_question = question
        orchestrator.run_state.pending_options = options
        orchestrator.run_state.pending_questions = [{"ask": question, "options": options}]
        return ToolResult(
            ok=True,
            data={"pending": True, "question": question, "options": options},
        )

    registry.register(ASK_USER, _ask_user)
