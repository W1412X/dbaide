"""LLM tool-calling loop for Ask agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from dbaide.agent.runtime import AgentRuntime
from dbaide.agent.toolkit import build_tool_registry
from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.result import ExecutionPolicy
from dbaide.llm import LLMMessage
from dbaide.models import AssistantResponse
from dbaide.tools.registry import ToolContext, ToolResult

if True:  # TYPE_CHECKING without circular import at runtime
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.loop")

DECISION_RETRIES = 2
RESULT_PREVIEW_LIMIT = 3500


class LoopDecisionError(RuntimeError):
    """LLM returned an invalid or empty loop decision."""


@dataclass(slots=True)
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    ok: bool
    summary: str


@dataclass(slots=True)
class LoopState:
    question: str
    database: str
    execute_allowed: bool
    calls: list[ToolCallRecord] = field(default_factory=list)


class AskAgentLoop:
    """Codex-style tool loop: LLM chooses tools until it finishes with an answer."""

    def __init__(self, orchestrator: AskOrchestrator, *, progress: Callable[[str], None] | None = None) -> None:
        self.orchestrator = orchestrator
        self.progress = progress or orchestrator.progress
        self.registry = build_tool_registry(orchestrator)

    def run(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        disclosures_before: list[str] | None = None,
    ) -> AssistantResponse | None:
        """Run the tool loop. Returns None to signal fallback to staged pipeline."""
        orch = self.orchestrator
        orch._reset_loop_state(question, database, execute)
        orch.schema.disclose_instance()

        state = LoopState(question=question, database=database, execute_allowed=execute)
        tool_ctx = ToolContext(
            execution_policy=orch.execution_policy.value,
            trace_sink=self._trace_sink,
        )
        runtime = AgentRuntime(
            llm=orch.llm,
            tool_registry=self.registry,
            execution_policy=orch.execution_policy,
            trace_sink=self._trace_sink,
        )

        self.progress("Agent loop started…")
        transcript: list[str] = []

        while runtime.steps_remaining > 0:
            try:
                decision = self._decide(state, transcript)
            except LoopDecisionError as exc:
                logger.warning("loop_decision_failed: %s", exc)
                return None

            action = str(decision.get("action") or "").strip().lower()
            if action == "finish":
                answer = str(decision.get("answer") or "").strip()
                if not answer or answer == "Query complete.":
                    answer = self._answer_from_state(orch)
                if not answer:
                    return None
                self.progress("Agent loop finished")
                return self._build_response(orch, answer, disclosures_before or [])

            if action != "call_tool":
                logger.warning("loop_unknown_action: %s", action)
                return None

            tool_name = str(decision.get("tool") or "").strip()
            if tool_name not in self.registry._handlers:  # noqa: SLF001
                transcript.append(f"Error: unknown tool {tool_name!r}. Use a registered tool name.")
                continue

            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            thought = str(decision.get("thought") or "").strip()
            if thought:
                self.progress(thought[:120])

            runtime.trace_step(stage=tool_name, title=f"Calling {tool_name}")
            result = runtime.call_tool(tool_name, args, tool_ctx)
            summary = _summarize_tool_result(tool_name, result)
            state.calls.append(ToolCallRecord(tool=tool_name, args=args, ok=result.ok, summary=summary))
            transcript.append(f"Tool `{tool_name}` → {summary}")

            if tool_name == "execute_sql" and result.ok:
                break
            if tool_name == "execute_sql" and isinstance(result.data, dict) and result.data.get("blocked"):
                sql = str(result.data.get("sql") or orch._loop_sql or "")
                reason = str(result.data.get("reason") or "Execution blocked")
                orch._loop_answer = f"SQL:\n```sql\n{sql}\n```\n\n_{reason}_"
                break
            if tool_name == "validate_sql" and result.ok:
                policy = orch.execution_policy
                if not state.execute_allowed or policy.value in ("sql_only", "inspect_only"):
                    sql = orch._loop_sql or ""
                    orch._loop_answer = f"SQL:\n```sql\n{sql}\n```\n\n_Generated (not executed)._"
                    break
            if tool_name == "synthesize_schema_answer" and result.ok:
                break
            if tool_name == "profile_table" and result.ok:
                break

        if orch._loop_query_result or orch._loop_answer:
            if orch._loop_query_result:
                answer = self._answer_from_state(orch)
            else:
                answer = orch._loop_answer or self._answer_from_state(orch)
            if answer:
                return self._build_response(orch, answer, disclosures_before or [])

        logger.warning("loop_budget_exhausted steps=%d", len(state.calls))
        return None

    def _decide(self, state: LoopState, transcript: list[str]) -> dict[str, Any]:
        tools = self.registry.list_specs()
        tool_lines = "\n".join(f"- {s.name}: {s.description}" for s in tools)
        policy = self.orchestrator.execution_policy.value
        execute_note = "allowed" if state.execute_allowed else "disabled"

        system = (
            "You are DBAide, a database assistant operating in a tool loop.\n"
            "Choose the next action to answer the user. Use progressive discovery before guessing schema.\n\n"
            f"Execution policy: {policy} (execute_sql is {execute_note})\n\n"
            "Available tools:\n"
            f"{tool_lines}\n\n"
            "Return JSON only:\n"
            '  {"action":"call_tool","tool":"discover_schema","args":{"question":"..."},"thought":"..."}\n'
            '  {"action":"finish","answer":"markdown answer for the user"}\n\n'
            "Guidelines:\n"
            "- Schema / where-is questions: discover_schema → synthesize_schema_answer → finish\n"
            "- Data queries: discover_schema → describe_table (each relevant table)"
            " → get_relations (when multiple tables) → generate_sql → validate_sql"
            + (" → execute_sql → finish" if state.execute_allowed and policy not in ("sql_only", "inspect_only") else " → finish")
            + "\n"
            "- Multi-table: describe every needed table; get_relations loads declared FKs; generate_sql uses all disclosed tables.\n"
            "- If validate_sql reports unknown tables/columns, describe_table then retry generate_sql.\n"
            "- Profile questions: discover_schema → profile_table → finish\n"
            "- SQL explain: validate_sql or explain_sql as needed → finish\n"
            "- Do not invent tables or columns. Prefer precision over listing everything.\n"
            "- When you have enough to answer, use action=finish."
        )

        history = "\n\n".join(transcript[-8:]) if transcript else "(no tool calls yet)"
        user = (
            f"User question:\n{state.question}\n\n"
            f"Database scope: {state.database or '(any)'}\n\n"
            f"Tool history:\n{history}"
        )

        last_error = ""
        for attempt in range(DECISION_RETRIES):
            messages = [LLMMessage("system", system), LLMMessage("user", user)]
            if last_error:
                messages.append(LLMMessage("user", f"Previous decision invalid: {last_error}. Try again."))
            payload = self.orchestrator.llm.complete_json(
                messages,
                schema_hint='Return {"action":"call_tool|finish","tool":"...","args":{},"thought":"...","answer":"..."}',
            )
            if not isinstance(payload, dict) or not payload.get("action"):
                last_error = "missing action field"
                continue
            action = str(payload.get("action")).lower()
            if action == "finish":
                return payload
            if action == "call_tool" and payload.get("tool"):
                return payload
            last_error = f"invalid action payload: {payload!r}"
        raise LoopDecisionError(last_error or "no valid decision")

    def _answer_from_state(self, orch: AskOrchestrator) -> str:
        if orch._loop_query_result and orch._loop_sql:
            draft_rationale = orch._loop_sql_rationale or "Generated by agent loop"
            interpretation = orch.interpreter.interpret(
                question=orch._loop_question or "",
                sql=orch._loop_sql,
                row_count=orch._loop_query_result.row_count,
                columns=orch._loop_query_result.columns,
                elapsed_ms=orch._loop_query_result.elapsed_ms,
                truncated=orch._loop_query_result.truncated,
                warnings=[],
            )
            return orch.formatter.query_result(
                orch._loop_query_result,
                rationale=draft_rationale,
                interpretation=interpretation,
            )
        if orch._loop_sql and not orch._loop_query_result:
            note = "SQL generated (not executed)."
            return f"SQL:\n```sql\n{orch._loop_sql}\n```\n\n_{note}_"
        return orch._loop_answer or ""

    def _build_response(
        self, orch: AskOrchestrator, answer: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        return AssistantResponse(
            answer=answer,
            sql=orch._loop_sql or "",
            result=orch._loop_query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
        )

    def _trace_sink(self, event: TraceEvent) -> None:
        if event.title:
            self.progress(event.title[:120])


def _summarize_tool_result(tool: str, result: ToolResult) -> str:
    if not result.ok and result.error:
        return f"ERROR: {result.error.message}"
    data = result.data
    if data is None:
        return "ok (empty)"
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > RESULT_PREVIEW_LIMIT:
        return text[:RESULT_PREVIEW_LIMIT] + "…[truncated]"
    return text
