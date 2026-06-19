"""Session-turn retrieval tools.

Provides cross-turn access: fetch a prior turn's full clarifications/SQL/answer/
disclosed tables, or page back to turns before the default visible window.
"""
from __future__ import annotations

from typing import Any

from dbaide.agent.toolkit.support import _err, _string_list
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import LIST_EARLIER_TURNS, RETRIEVE_TURN

_DEFAULT_PRIOR_WINDOW = 3
_LIST_EARLIER_DEFAULT_LIMIT = 5


def _resolve_turn_index(orchestrator, turn_id: str) -> int:
    raw = str(turn_id or "").strip().lower()
    if not raw.startswith("t"):
        return -1
    try:
        idx = int(raw[1:]) - 1
    except ValueError:
        return -1
    turns = orchestrator.session_turns or []
    if idx < 0 or idx >= len(turns):
        return -1
    return idx


def _answer_summary(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def register(registry: ToolRegistry, orchestrator) -> None:
    def _retrieve_turn(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        turn_id = str(args.get("turn_id") or "").strip()
        idx = _resolve_turn_index(orchestrator, turn_id)
        if idx < 0:
            total = len(orchestrator.session_turns or [])
            return ToolResult(
                ok=False,
                error=_err(
                    "retrieve_turn",
                    f"unknown turn_id {turn_id!r}; this session has {total} turn(s) "
                    f"(t1..t{total}); use list_earlier_turns to discover ids.",
                ),
            )
        turn = orchestrator.session_turns[idx]
        include = _string_list(args.get("include"))
        all_fields = {"question", "status", "clarifications", "sql", "answer", "tables", "memory"}
        wanted = set(include) if include else all_fields
        invalid = sorted(wanted - all_fields)
        if invalid:
            return ToolResult(
                ok=False,
                error=_err(
                    "retrieve_turn",
                    f"unknown include field(s): {', '.join(invalid)}. "
                    f"Valid: {', '.join(sorted(all_fields))}",
                ),
            )
        data: dict[str, Any] = {
            "turn_id": f"t{idx + 1}",
            "created_at": turn.get("created_at"),
        }
        if "question" in wanted:
            data["question"] = str(turn.get("question") or "")
        if "status" in wanted:
            data["status"] = str(turn.get("status") or "")
        if "clarifications" in wanted:
            data["clarifications"] = [str(x) for x in (turn.get("clarifications") or [])]
        if "sql" in wanted:
            data["selected_sql"] = str(turn.get("selected_sql") or "")
            data["executed_sqls"] = [
                dict(item)
                for item in (turn.get("executed_sqls") or [])
                if isinstance(item, dict)
            ]
        if "answer" in wanted:
            data["answer_markdown"] = str(turn.get("answer_markdown") or "")
        if "tables" in wanted:
            data["disclosed_tables"] = [str(x) for x in (turn.get("disclosed_tables") or [])]
        if "memory" in wanted:
            data["verified_facts"] = [str(x) for x in (turn.get("verified_facts") or [])]
            data["excluded_paths"] = [
                dict(item)
                for item in (turn.get("excluded_paths") or [])
                if isinstance(item, dict)
            ]
        return ToolResult(ok=True, data=data)

    def _list_earlier_turns(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        turns = orchestrator.session_turns or []
        total = len(turns)
        try:
            offset = int(args.get("offset")) if args.get("offset") is not None else 0
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(args.get("limit")) if args.get("limit") is not None else _LIST_EARLIER_DEFAULT_LIMIT
        except (TypeError, ValueError):
            limit = _LIST_EARLIER_DEFAULT_LIMIT
        offset = max(0, offset)
        limit = max(1, limit)
        end = min(offset + limit, total)
        slice_ = turns[offset:end]
        out = []
        for i, turn in enumerate(slice_):
            real_idx = offset + i
            out.append({
                "turn_id": f"t{real_idx + 1}",
                "question": str(turn.get("question") or ""),
                "answer_summary": _answer_summary(str(turn.get("answer_markdown") or "")),
                "created_at": turn.get("created_at"),
            })
        return ToolResult(ok=True, data={
            "turns": out,
            "total": total,
            "more": end < total,
            "window_size": getattr(orchestrator.session, "prior_turns_window", _DEFAULT_PRIOR_WINDOW),
        })

    registry.register(RETRIEVE_TURN, _retrieve_turn)
    registry.register(LIST_EARLIER_TURNS, _list_earlier_turns)
