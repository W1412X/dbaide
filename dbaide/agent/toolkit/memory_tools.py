"""Working-memory retrieval tools."""
from __future__ import annotations

from typing import Any

from dbaide.agent.toolkit.support import _err
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import RETRIEVE_MEMORY_ITEM


def register(registry: ToolRegistry, orchestrator) -> None:
    def _retrieve_memory_item(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ref = str(args.get("ref") or "").strip()
        if not ref:
            return ToolResult(ok=False, error=_err("retrieve_memory_item", "ref is required"))
        item = orchestrator.run_state.memory.retrieve_archive(ref)
        if item is None:
            return ToolResult(ok=False, error=_err("retrieve_memory_item", f"memory ref not found: {ref}"))
        return ToolResult(
            ok=True,
            data={
                "id": item.id,
                "action": item.action,
                "summary": item.summary,
                "source_refs": list(item.source_refs),
                "payload": item.payload,
            },
        )

    registry.register(RETRIEVE_MEMORY_ITEM, _retrieve_memory_item)
