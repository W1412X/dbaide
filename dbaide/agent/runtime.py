"""Agent runtime for DBAide - manages agent execution context and tool injection."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.result import ExecutionPolicy, WorkflowResult
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

    MAX_STEPS = 12

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        tool_registry: ToolRegistry | None = None,
        execution_policy: ExecutionPolicy = ExecutionPolicy.SAFE_AUTO,
        trace_sink: Callable[[TraceEvent], None] | None = None,
    ) -> None:
        self.llm = llm or NullLLMClient()
        self.tool_registry = tool_registry or ToolRegistry()
        self.execution_policy = execution_policy
        self.trace_sink = trace_sink or (lambda _: None)
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
        return max(0, self.MAX_STEPS - self._step_count)

    def check_budget(self) -> None:
        """Check if execution budget is exhausted."""
        if self._cancelled:
            raise RuntimeError("Execution cancelled by user")
        if self._step_count >= self.MAX_STEPS:
            raise RuntimeError(f"Execution budget exhausted ({self.MAX_STEPS} steps)")

    def emit_trace(self, event: TraceEvent) -> None:
        """Emit a trace event."""
        self.trace_sink(event)

    def trace_step(self, *, stage: str, title: str, kind: TraceKind = TraceKind.AGENT) -> None:
        """Record a step in the trace."""
        self._step_count += 1
        self.emit_trace(TraceEvent(
            level=TraceLevel.INFO,
            kind=kind,
            stage=stage,
            actor="runtime",
            title=title,
            status="running",
        ))

    def trace_tool_call(self, tool_name: str, args: dict[str, Any]) -> ToolContext:
        """Prepare a tool call context with trace."""
        self.check_budget()
        self._step_count += 1
        return ToolContext(
            trace_sink=self.trace_sink,
            execution_policy=self.execution_policy.value,
        )

    def call_tool(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Call a tool with budget checking. Each call consumes one step so the
        agent loop (driven by ``steps_remaining``) is actually bounded by MAX_STEPS."""
        self.check_budget()
        self._step_count += 1
        return self.tool_registry.invoke(name, args, ctx)

    def elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.perf_counter() - self._start_time) * 1000

    def build_error(self, code: ErrorCode, stage: str, message: str, *, hint: str = "",
                    retryable: bool = False, repair: RepairAction = RepairAction.STOP) -> DBAideError:
        """Build a structured error with trace."""
        error = DBAideError(
            code=code,
            stage=stage,
            message=message,
            hint=hint,
            retryable=retryable,
            repair_action=repair,
        )
        self.emit_trace(TraceEvent(
            level=TraceLevel.ERROR,
            kind=TraceKind.AGENT,
            stage=stage,
            actor="runtime",
            title=f"Error: {code.value}",
            summary=message,
            status="failed",
        ))
        return error
