"""Agent runtime for DBAide - manages agent execution context and tool injection."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from dbaide.core.cancellation import CancelledError
from dbaide.core.events import TraceEvent
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult

logger = logging.getLogger("dbaide.runtime")


class AgentRuntime:
    """Agent runtime that manages tool injection, trace, and execution budget.

    Inspired by Claude Code's runtime:
    - Injects system prompt and available tools
    - Tracks execution steps and budget
    - Records token/latency/errors
    - Supports cancellation
    """

    MAX_STEPS = 64  # default; overridable per-run via the ``max_steps`` argument

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        tool_registry: ToolRegistry | None = None,
        trace_sink: Callable[[TraceEvent], None] | None = None,
        max_steps: int | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        self.llm = llm or NullLLMClient()
        self.tool_registry = tool_registry or ToolRegistry()
        self.trace_sink = trace_sink or (lambda _: None)
        self.max_steps = max(1, int(max_steps)) if max_steps else self.MAX_STEPS
        self.cancel_check = cancel_check
        self._step_count = 0
        self._cancelled = False
        self._start_time = time.perf_counter()

    def cancel(self) -> None:
        """Cancel the current execution."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self._step_count)

    def check_budget(self) -> None:
        """Check if execution budget is exhausted."""
        self.check_cancelled()
        if self._cancelled:
            raise CancelledError("Execution cancelled by user")
        if self._step_count >= self.max_steps:
            raise RuntimeError(f"Execution budget exhausted ({self.max_steps} steps)")

    def check_cancelled(self) -> None:
        if self.cancel_check:
            self.cancel_check()
        if self._cancelled:
            raise CancelledError("Execution cancelled by user")

    def emit_trace(self, event: TraceEvent) -> None:
        """Emit a trace event."""
        self.trace_sink(event)

    def call_tool(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Call a tool with budget checking. Each call consumes one step so the
        agent loop (driven by ``steps_remaining``) is actually bounded by MAX_STEPS."""
        self.check_budget()
        self._step_count += 1
        result = self.tool_registry.invoke(name, args, ctx)
        self.check_cancelled()
        return result

    def consume_step(self) -> None:
        """Charge one step without invoking a tool (e.g. an unknown-tool attempt),
        so non-tool iterations of the loop still count against the budget."""
        self.check_budget()
        self._step_count += 1

    def elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.perf_counter() - self._start_time) * 1000
