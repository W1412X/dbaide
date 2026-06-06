"""Tool registry for DBAide agent tools."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.tools.specs import ToolSpec

logger = logging.getLogger("dbaide.tools.registry")


class ToolResult:
    """Result of a tool invocation."""

    __slots__ = ("ok", "data", "error", "duration_ms")

    def __init__(
        self,
        *,
        ok: bool = True,
        data: Any = None,
        error: DBAideError | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        self.ok = ok
        self.data = data
        self.error = error
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        result = {"ok": self.ok, "duration_ms": self.duration_ms}
        if self.data is not None:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error.to_dict()
        return result


class ToolContext:
    """Context passed to tool handlers."""

    __slots__ = (
        "workflow_id", "connection", "adapter", "asset_store",
        "session", "execution_policy", "trace_sink", "cancel_check",
    )

    def __init__(
        self,
        *,
        workflow_id: str = "",
        connection: Any = None,
        adapter: Any = None,
        asset_store: Any = None,
        session: Any = None,
        execution_policy: str = "safe_auto",
        trace_sink: Callable[[TraceEvent], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.connection = connection
        self.adapter = adapter
        self.asset_store = asset_store
        self.session = session
        self.execution_policy = execution_policy
        self.trace_sink = trace_sink
        self.cancel_check = cancel_check

    def emit_trace(self, event: TraceEvent) -> None:
        if self.trace_sink:
            self.trace_sink(event)

    def check_cancelled(self) -> None:
        if self.cancel_check:
            self.cancel_check()


class ToolRegistry:
    """Registry for agent tools with specs, permissions, and tracing."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._cache: dict[str, tuple[float, Any]] = {}

    def register(self, spec: ToolSpec, handler: Callable[..., Any]) -> None:
        """Register a tool with its spec and handler."""
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        logger.debug("registered tool: %s (permission=%s)", spec.name, spec.permission_level)

    def spec(self, name: str) -> ToolSpec:
        """Get tool spec by name."""
        if name not in self._specs:
            raise KeyError(f"Unknown tool: {name}")
        return self._specs[name]

    def list_specs(self, permission_level: str | None = None) -> list[ToolSpec]:
        """List all tool specs, optionally filtered by permission level."""
        specs = list(self._specs.values())
        if permission_level:
            specs = [s for s in specs if s.permission_level == permission_level]
        return specs

    def invoke(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Invoke a tool with arguments and context."""
        ctx.check_cancelled()
        if name not in self._specs:
            return ToolResult(
                ok=False,
                error=DBAideError(
                    code=ErrorCode.VALIDATION_FAILED,
                    stage="tool_registry",
                    message=f"Unknown tool: {name}",
                    hint="Check tool name spelling",
                    repair_action=RepairAction.STOP,
                ),
            )

        spec = self._specs[name]
        handler = self._handlers[name]

        # Check cache
        cache_key = f"{name}:{hash(json.dumps(arguments, sort_keys=True, default=str))}"
        if spec.cache_policy == "session" and cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            logger.debug("cache hit: %s", name)
            return cached

        # Emit trace start
        start = time.perf_counter()
        ctx.emit_trace(TraceEvent(
            workflow_id=ctx.workflow_id,
            level=TraceLevel.INFO,
            kind=TraceKind.TOOL,
            stage=name,
            actor="tool",
            title=f"Tool: {name}",
            summary=spec.description,
            input_preview=str(arguments)[:200],
            status="running",
        ))

        try:
            ctx.check_cancelled()
            result = handler(arguments, ctx)
            ctx.check_cancelled()
            elapsed = (time.perf_counter() - start) * 1000

            if not isinstance(result, ToolResult):
                result = ToolResult(ok=True, data=result, duration_ms=elapsed)
            result.duration_ms = elapsed

            # Emit trace end
            ctx.emit_trace(TraceEvent(
                workflow_id=ctx.workflow_id,
                level=TraceLevel.INFO,
                kind=TraceKind.TOOL,
                stage=name,
                actor="tool",
                title=f"Tool: {name}",
                summary=f"Completed in {elapsed:.0f}ms",
                output_preview=str(result.data)[:200] if result.data else "",
                duration_ms=elapsed,
                status="completed" if result.ok else "failed",
            ))

            # Cache result
            if spec.cache_policy == "session" and result.ok:
                self._cache[cache_key] = (time.perf_counter(), result)

            return result

        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            error = DBAideError.from_exception(exc, stage=name, code=ErrorCode.VALIDATION_FAILED)

            ctx.emit_trace(TraceEvent(
                workflow_id=ctx.workflow_id,
                level=TraceLevel.ERROR,
                kind=TraceKind.TOOL,
                stage=name,
                actor="tool",
                title=f"Tool: {name} failed",
                summary=str(exc)[:200],
                duration_ms=elapsed,
                status="failed",
            ))

            return ToolResult(ok=False, error=error, duration_ms=elapsed)
