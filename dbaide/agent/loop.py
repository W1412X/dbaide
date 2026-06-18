"""LLM tool-calling loop for Ask agent — conversation-stream architecture.

The model sees a growing ``messages: list[LLMMessage]``. Each tool result is
appended as a user message with smart per-tool formatting. When the estimated
token count approaches the context budget, older messages are compressed via an
LLM summarization call that preserves SQL results, verified facts, schema
evidence, and excluded paths.  The system prompt (messages[0]) and the initial
user question (messages[1]) are NEVER compressed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from dbaide.agent.answer_stream import JsonFieldStreamer
from dbaide.agent.loop_state import dump_loop_state, restore_loop_state
from dbaide.agent.progress_events import brief_tool_summary, from_trace_event, progress_event
from dbaide.agent.sql_executions import response_sql_exports
from dbaide.agent.loop_prompts import DecisionPromptBuilder, estimate_tokens, tool_prompt_line
from dbaide.agent.llm_trace import llm_stage
from dbaide.agent.runtime import AgentRuntime
from dbaide.agent.toolkit import build_tool_registry, loop_tool_specs
from dbaide.agent.toolkit.result_preview import preview_rows
from dbaide.core.cancellation import CancelledError
from dbaide.core.events import TraceEvent
from dbaide.llm import LLMMessage, ToolsUnsupported as _ToolsUnsupported
from dbaide.models import AssistantResponse
from dbaide.tools.registry import ToolContext, ToolResult

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.loop")

DECISION_RETRIES = 2
_EXECUTE_TOOLS = frozenset({"execute_sql"})

BATCHABLE_TOOLS = frozenset({
    "discover_schema", "retrieve_schema_context", "list_databases", "list_tables",
    "describe_table", "inspect_metadata", "retrieve_join_context",
    "column_stats", "profile_table",
    "retrieve_turn", "list_earlier_turns",
})
_DEFAULT_MAX_BATCH = 6
_SQL_TOOLS = frozenset({"execute_sql", "explain_sql",
                        "generate_sql", "validate_sql"})

# Default compress threshold (percentage): compress when token estimate exceeds
# this percentage of context_length.
_DEFAULT_COMPRESS_THRESHOLD = 80
# Default context length when model config doesn't specify one.
_DEFAULT_CONTEXT_LENGTH = 32000
# Soft cap on individual tool result text in conversation messages (chars).
_RESULT_SOFT_CAP = 8000

_STUCK_LOOP_THRESHOLD = 3
_STUCK_LOOP_ESCALATION = 5


def _max_tail_keep_index(token_sizes: list[int], threshold: int, *, head: int = 2, overhead: int = 0) -> int:
    """Largest tail to preserve under a hard token budget.

    Returns the SMALLEST ``first_keep`` (>= ``head``) such that the ``head`` leading
    messages + ``overhead`` + the tail ``token_sizes[first_keep:]`` fit within
    ``threshold``. Callers drop ``messages[head:first_keep]`` (replacing them with a
    truncation note). The point is to keep as MANY recent messages as fit — walking
    from the end and stopping when the next-older message would overflow, not stopping
    at the first message that fits (which would keep only the single last message).
    """
    n = len(token_sizes)
    base = sum(token_sizes[:head]) + overhead
    cumulative = 0
    first_keep = n  # default: keep no tail messages (only head + note)
    for i in range(n - 1, head - 1, -1):
        if base + cumulative + token_sizes[i] > threshold:
            break
        cumulative += token_sizes[i]
        first_keep = i
    return first_keep


def _executed_sql(tool_name: str, orch, result) -> str:
    if tool_name not in _SQL_TOOLS:
        return ""
    data = getattr(result, "data", None)
    if isinstance(data, dict) and data.get("sql"):
        return str(data["sql"]).strip()
    return str(orch.run_state.sql or "").strip()


def _risk_reply_confirms(reply: str) -> bool:
    """Approve a risk-gated execution ONLY on the exact UI-button phrases. This is
    deliberately strict: a risk gate guards a potentially heavy/expensive query, so
    anything ambiguous (or merely containing "执行") must default to cancel, not run.
    The UI surfaces these as clickable buttons, so the common path is unambiguous."""
    text = " ".join(str(reply or "").split()).casefold()
    approved = {"execute anyway", "仍然执行"}
    return text in {item.casefold() for item in approved}


class LoopDecisionError(RuntimeError):
    """LLM returned an invalid or empty loop decision."""
    def __init__(self, message: str, *, raw_payload: str = ""):
        super().__init__(message)
        self.raw_payload = raw_payload


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
    answer_language: str = "en"
    calls: list[ToolCallRecord] = field(default_factory=list)


class AskAgentLoop:
    """Conversation-stream tool loop: LLM sees a growing message list until it finishes."""

    def __init__(self, orchestrator: AskOrchestrator, *, progress: Callable[[str], None] | None = None) -> None:
        self.orchestrator = orchestrator
        self.progress = progress or orchestrator.progress
        self.registry = build_tool_registry(orchestrator)
        self.prompts = DecisionPromptBuilder(orchestrator)
        self.allowed_tool_specs = loop_tool_specs(self.registry)
        self.allowed_tool_names = frozenset(s.name for s in self.allowed_tool_specs)
        self._trace_parent = ""
        self._agent_loop_node_id = ""
        # Native tool-calling: built lazily; flips to True if the endpoint rejects
        # tools so we stop retrying native and use the JSON protocol for the run.
        self._tool_schemas_cache: list[dict[str, Any]] | None = None
        self._tools_downgraded = False

    def _ns_step(self, event: dict) -> dict:
        if self._trace_parent and event.get("step"):
            event["node_id"] = f"{self._trace_parent}:step:{event['step']}"
            event["parent_id"] = self._agent_loop_node_id or self._trace_parent
        elif event.get("step") and self._agent_loop_node_id:
            event["parent_id"] = self._agent_loop_node_id
        return event

    def _context_budget(self) -> int:
        model_cfg = getattr(self.orchestrator, "model_config", None)
        ctx_len = getattr(model_cfg, "context_length", _DEFAULT_CONTEXT_LENGTH) if model_cfg else _DEFAULT_CONTEXT_LENGTH
        return max(ctx_len, 8000)

    def _reported_prompt_tokens(self, orch: AskOrchestrator) -> int:
        """Exact prompt-token count the API reported for the most recent LLM
        call, or 0 if unavailable (no call yet, or the endpoint omits usage)."""
        usage = getattr(getattr(orch, "llm", None), "last_usage", None)
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            if isinstance(pt, int) and pt > 0:
                return pt
        return 0

    def _speculative_prefetch(
        self, orch: AskOrchestrator, question: str, database: str,
    ) -> ToolResult | None:
        if not question.strip():
            return None
        scope = orch.schema_scope if orch.schema_scope else None
        tool_ctx = ToolContext(
            workflow_id=self._trace_parent or "prefetch",
            connection=orch.session.connection,
            adapter=orch.adapter,
            asset_store=orch.asset_store,
            session=orch.session,
            trace_sink=self._trace_sink,
            cancel_check=orch.cancel_check,
        )
        args: dict[str, Any] = {"request": question, "database": database}
        if scope:
            args["scope"] = scope
        try:
            return self.registry.invoke("retrieve_schema_context", args, tool_ctx)
        except Exception:
            return None

    def _inject_prefetch(
        self, orch: AskOrchestrator, messages: list[LLMMessage], result: ToolResult,
    ) -> None:
        """Append a successful prefetch result into the conversation."""
        formatted = _format_tool_result("retrieve_schema_context", result)
        messages.append(LLMMessage("user", f"[Tool result: retrieve_schema_context]\n{formatted}"))
        orch.run_state.schema_prefetched = True
        prefetch_node = f"{self._trace_parent}:prefetch" if self._trace_parent else "prefetch"
        summary = brief_tool_summary("retrieve_schema_context", result)
        ev = progress_event(
            stage="retrieve_schema_context",
            title="Schema prefetch",
            status="completed",
            kind="tool",
            detail=(summary or "")[:200],
        )
        ev["node_id"] = prefetch_node
        ev["parent_id"] = self._agent_loop_node_id or self._trace_parent
        self.progress(ev)

    def run(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        disclosures_before: list[str] | None = None,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
        trace_parent: str = "",
        answer_language: str | None = None,
        session_messages: list[LLMMessage] | None = None,
    ) -> AssistantResponse:
        orch = self.orchestrator
        self._trace_parent = trace_parent
        self._agent_loop_node_id = f"{trace_parent}:loop" if trace_parent else "loop"
        self._session_messages = session_messages
        self._turn_number = 0
        messages: list[LLMMessage] = []
        approved_risk_sql = ""
        approved_risk_args: dict[str, Any] = {}
        approved_risk_tool = "execute_sql"

        if resume_state:
            messages, execute = restore_loop_state(orch, resume_state)
            if session_messages is not None:
                self._turn_number = self._count_completed_turns(messages) + 1
            reply = str(user_reply or question or "").strip()
            if reply:
                messages.append(LLMMessage("user", f"User reply: {reply}"))
                pending_question = (
                    orch.run_state.clarify_questions
                    or orch.run_state.pending_question
                    or "pending clarification"
                )
                if pending_question and not orch.run_state.risk_confirmation:
                    fact = (
                        f"User confirmed the following criteria — {pending_question}\n"
                        f"User's answer: {reply}"
                    )
                    if fact not in orch.run_state.clarifications:
                        orch.run_state.clarifications.append(fact)
                        orch.run_state.clarifications = orch.run_state.clarifications[-12:]
                    if fact not in orch.run_state.memory.confirmed_facts:
                        orch.run_state.memory.confirmed_facts.append(fact)
                        orch.run_state.memory.confirmed_facts = orch.run_state.memory.confirmed_facts[-12:]
                    orch.run_state.clarify_questions = ""
                    orch.run_state.pending_question = ""
                    orch.run_state.pending_options = []
                    orch.run_state.pending_questions = []
                if orch.run_state.risk_confirmation:
                    risk = dict(orch.run_state.risk_confirmation)
                    if _risk_reply_confirms(reply):
                        sql_hash = str(risk.get("sql_hash") or "").strip()
                        approved_risk_sql = str(risk.get("sql") or orch.run_state.sql or "").strip()
                        approved_risk_args = dict(risk.get("execute_args") or {})
                        approved_risk_tool = str(risk.get("tool") or "execute_sql").strip() or "execute_sql"
                        if approved_risk_tool not in _EXECUTE_TOOLS:
                            approved_risk_tool = "execute_sql"
                        if approved_risk_sql:
                            approved_risk_args["sql"] = approved_risk_sql
                        if sql_hash and sql_hash not in orch.run_state.confirmed_risk_sqls:
                            orch.run_state.confirmed_risk_sqls.append(sql_hash)
                        orch.run_state.risk_confirmation = {}
                        orch.run_state.pending_question = ""
                        orch.run_state.pending_options = []
                        orch.run_state.pending_questions = []
                        messages.append(LLMMessage("user", "Risk confirmation: user approved execution."))
                    else:
                        sql = str(risk.get("sql") or orch.run_state.sql or "").strip()
                        cancelled = "执行已取消。" if orch.run_state.answer_language == "zh" else "Execution cancelled."
                        orch.run_state.answer = (
                            f"{cancelled}\n\n"
                            f"SQL:\n```sql\n{sql}\n```"
                        )
                        orch.run_state.risk_confirmation = {}
                        orch.run_state.pending_question = ""
                        orch.run_state.pending_options = []
                        orch.run_state.pending_questions = []
                        self.progress(
                            progress_event(stage="user", title=f"Reply: {reply[:120]}", status="completed", kind="user"),
                        )
                        return self._build_response(orch, orch.run_state.answer, disclosures_before or [])
                self.progress(
                    progress_event(stage="user", title=f"Reply: {reply[:120]}", status="completed", kind="user"),
                )
            state = LoopState(
                question=str(resume_state.get("question") or question),
                database=str(resume_state.get("database") or database),
                execute_allowed=execute,
                answer_language=orch.run_state.answer_language,
            )
            self.progress(
                progress_event(
                    stage="loop",
                    title="Agent loop",
                    status="running",
                    kind="phase",
                    node_id=self._agent_loop_node_id,
                    parent_id=self._trace_parent,
                    detail="resuming",
                ),
            )
            if orch.run_state.answer and not orch.run_state.query_result and not reply:
                return self._build_response(orch, orch.run_state.answer, disclosures_before or [])
        else:
            orch._reset_loop_state(question, database, execute, answer_language=answer_language)
            orch.schema.disclose_instance()
            state = LoopState(
                question=question,
                database=database,
                execute_allowed=execute,
                answer_language=orch.run_state.answer_language,
            )
            tools = self.allowed_tool_specs
            tool_lines = "\n".join(tool_prompt_line(s) for s in tools)
            execute_note = "allowed" if state.execute_allowed else "disabled"

            if session_messages is not None and len(session_messages) >= 2:
                # Session continuity: reuse existing message stream
                messages = list(session_messages)
                messages[0] = LLMMessage("system",
                    self.prompts.system_prompt(state, tool_lines, execute_note))
                self._turn_number = self._count_completed_turns(messages) + 1
                user_msg = self.prompts.session_turn_prompt(state, self._turn_number)
                messages.append(LLMMessage("user",
                    f"[turn:{self._turn_number}:start]\n{user_msg}"))
                prefetch_result = self._speculative_prefetch(orch, question, database)
                if prefetch_result and prefetch_result.ok:
                    self._inject_prefetch(orch, messages, prefetch_result)
                self._maybe_compress_turns(orch, messages)
            elif session_messages is not None:
                # Bootstrap: first turn in a session — add turn markers so the
                # next turn enters session continuity mode.
                system = self.prompts.system_prompt(state, tool_lines, execute_note)
                self._turn_number = 1
                user = self.prompts.initial_user_prompt(state)
                messages = [LLMMessage("system", system),
                            LLMMessage("user", f"[turn:1:start]\n{user}")]
                prefetch_result = self._speculative_prefetch(orch, question, database)
                if prefetch_result and prefetch_result.ok:
                    self._inject_prefetch(orch, messages, prefetch_result)
            else:
                # Per-turn isolation (no session)
                system = self.prompts.system_prompt(state, tool_lines, execute_note)
                user = self.prompts.initial_user_prompt(state)
                messages = [LLMMessage("system", system), LLMMessage("user", user)]
                prefetch_result = self._speculative_prefetch(orch, question, database)
                if prefetch_result and prefetch_result.ok:
                    self._inject_prefetch(orch, messages, prefetch_result)

            self.progress(
                progress_event(
                    stage="loop",
                    title="Agent loop",
                    status="running",
                    kind="phase",
                    node_id=self._agent_loop_node_id,
                    parent_id=self._trace_parent,
                    detail="started",
                ),
            )

        tool_ctx = ToolContext(
            workflow_id=self._trace_parent or "ask",
            connection=orch.session.connection,
            adapter=orch.adapter,
            asset_store=orch.asset_store,
            session=orch.session,
            trace_sink=self._trace_sink,
            cancel_check=orch.cancel_check,
        )
        runtime = AgentRuntime(
            llm=orch.llm,
            tool_registry=self.registry,
            trace_sink=self._trace_sink,
            max_steps=orch.session.agent_max_steps,
            cancel_check=orch.cancel_check,
        )

        risk_step = (int(resume_state.get("step_base") or 0) if resume_state else 0) + 1
        if approved_risk_sql:
            self.progress(
                self._ns_step(progress_event(
                    stage=approved_risk_tool,
                    title="Executing confirmed SQL",
                    status="running",
                    kind="tool",
                    detail=approved_risk_sql[:240],
                    step=risk_step,
                )),
            )
            result = runtime.call_tool(approved_risk_tool, approved_risk_args or {"sql": approved_risk_sql}, tool_ctx)
            brief = brief_tool_summary(approved_risk_tool, result)
            done_event = self._ns_step(progress_event(
                stage=approved_risk_tool,
                title=f"{approved_risk_tool} done",
                status="completed" if result.ok else "failed",
                kind="tool",
                detail=brief,
                step=risk_step,
            ))
            if result.ok:
                done_event["sql"] = approved_risk_sql
                data = result.data if isinstance(result.data, dict) else {}
                if data.get("purpose"):
                    done_event["purpose"] = str(data["purpose"])
                if data.get("row_count") is not None:
                    done_event["row_count"] = data.get("row_count")
            self.progress(done_event)
            if result.ok:
                formatted = _format_tool_result(approved_risk_tool, result)
                messages.append(LLMMessage("user", f"[Tool result: {approved_risk_tool}]\n{formatted}"))
            else:
                reason = (result.error.message if result.error else "") or brief or "confirmed_execution_failed"
                return self._build_failed_response(orch, reason, disclosures_before or [])

        step_no = int(resume_state.get("step_base") or 0) if resume_state else 0
        if approved_risk_sql:
            step_no += 1
        recorder = getattr(orch, "llm_recorder", None)

        while runtime.steps_remaining > 0:
            # ── Auto-compress if approaching context limit ──
            if self._session_messages is not None:
                self._maybe_compress_turns(orch, messages)
            else:
                self._maybe_compress(orch, messages)

            decide_start = recorder.snapshot_len() if recorder else 0
            try:
                with llm_stage("decide"):
                    decision = self._decide(messages)
            except LoopDecisionError as exc:
                logger.warning("loop_decision_failed: %s", exc)
                raw_context = f"\nYour last attempt was: {exc.raw_payload}" if exc.raw_payload else ""
                messages.append(LLMMessage("user",
                    f"Error: could not parse a valid decision ({exc}).{raw_context}\n"
                    "Return ONLY one JSON object with action='call_tool' "
                    "(tool + args) or action='finish' (answer)."
                ))
                self.progress(
                    progress_event(
                        stage="decide",
                        title="Invalid decision",
                        status="failed",
                        kind="llm",
                        detail=str(exc)[:240],
                        node_id=(
                            f"{self._trace_parent}:decision:{step_no + 1}"
                            if self._trace_parent else f"decision:{step_no + 1}"
                        ),
                        parent_id=self._agent_loop_node_id,
                    ),
                )
                runtime.consume_step()
                continue

            thought = str(decision.get("thought") or "")[:500]
            if thought:
                orch.run_state.thought_trace.append(thought)
                orch.run_state.thought_trace = orch.run_state.thought_trace[-20:]
            tool_llm_start = recorder.snapshot_len() if recorder else 0
            action = str(decision.get("action") or "").strip().lower()
            decision_calls = recorder.since(decide_start) if recorder is not None else []
            prompt_tokens = getattr(self, "_last_prompt_tokens", 0)
            ev = progress_event(
                stage="decide",
                title=str(decision.get("thought") or "Agent decision")[:200],
                status="completed",
                kind="llm",
                detail=f"action={action or '?'}",
                node_id=(
                    f"{self._trace_parent}:decision:{step_no + 1}"
                    if self._trace_parent else f"decision:{step_no + 1}"
                ),
                parent_id=self._agent_loop_node_id,
            )
            ev["decision"] = decision
            if prompt_tokens:
                ev["prompt_tokens"] = prompt_tokens
            if decision_calls:
                ev["llm_calls"] = decision_calls
            self.progress(ev)

            if action == "finish":
                answer = str(decision.get("answer") or "").strip()
                if not answer or answer == "Query complete.":
                    answer = self._answer_from_state(orch)
                if not answer:
                    messages.append(LLMMessage("user",
                        "Error: you called finish with an empty answer. "
                        "Provide the markdown answer, or call a tool to get what you need."))
                    runtime.consume_step()
                    continue
                # Record the finish decision in the conversation stream
                messages.append(LLMMessage("assistant", json.dumps(decision, ensure_ascii=False)))
                if recorder is not None:
                    finish_calls = recorder.since(tool_llm_start)
                    if finish_calls:
                        ev = progress_event(stage="finish", title="Finish",
                                            status="completed", kind="tool", step=step_no + 1)
                        ev["llm_calls"] = finish_calls
                        ev["output"] = answer
                        self.progress(self._ns_step(ev))
                # Session continuity: mark turn end and propagate messages
                if self._session_messages is not None:
                    messages.append(LLMMessage("user",
                        f"[turn:{self._turn_number}:end] Answer delivered."))
                    orch.session_messages = messages
                self.progress(
                    progress_event(
                        stage="loop",
                        title="Agent loop",
                        status="completed",
                        kind="phase",
                        node_id=self._agent_loop_node_id,
                        parent_id=self._trace_parent,
                        detail="finished",
                    ),
                )
                return self._build_response(orch, answer, disclosures_before or [])

            if action == "call_tools":
                calls, dropped = self._batch_calls(decision)
                if dropped:
                    messages.append(LLMMessage("user",
                        "Note: these can't be batched (issue them one at a time so each keeps "
                        f"its gate / depends on prior results): {', '.join(dropped)}."
                    ))
                if not calls:
                    messages.append(LLMMessage("user",
                        "Error: no batchable tool calls found. Use 'call_tool' (singular) "
                        "for tools that depend on prior results or require sequential execution."
                        if dropped else
                        "Error: action 'call_tools' needs a non-empty 'calls' list of "
                        'independent read-only tools ({"tool":...,"args":...}).'
                    ))
                    runtime.consume_step()
                    continue
                # Append assistant decision BEFORE tool results (correct message ordering)
                messages.append(LLMMessage("assistant", json.dumps(decision, ensure_ascii=False)))
                paused = False
                for call in calls:
                    if runtime.steps_remaining <= 0:
                        break
                    step_no += 1
                    llm_start = recorder.snapshot_len() if recorder is not None else 0
                    sig = self._run_tool_call(orch, state, messages, runtime, tool_ctx,
                                              decision, call["tool"], call["args"], step_no,
                                              llm_start, recorder)
                    if sig == "pending":
                        paused = True
                        break
                if paused:
                    if self._session_messages is not None:
                        orch.session_messages = messages
                    return self._build_wait_response(
                        orch, state, messages, disclosures_before or [], step_base=step_no,
                    )
                continue

            if action != "call_tool":
                logger.warning("loop_unknown_action: %s", action)
                messages.append(LLMMessage("user",
                    f"Error: unknown action {action!r}. Use 'call_tool' (one tool), "
                    "'call_tools' (several independent read-only tools), or 'finish'."))
                runtime.consume_step()
                continue

            tool_name = str(decision.get("tool") or "").strip()
            if tool_name not in self.allowed_tool_names:
                allowed = ", ".join(sorted(self.allowed_tool_names))
                messages.append(LLMMessage("user",
                    f"Error: tool {tool_name!r} is not available in this loop. "
                    f"Use one of the advertised tools: {allowed}."
                ))
                runtime.consume_step()
                continue

            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            step_no += 1
            llm_start = recorder.snapshot_len() if recorder is not None else 0
            # Append assistant decision to conversation
            messages.append(LLMMessage("assistant", json.dumps(decision, ensure_ascii=False)))
            sig = self._run_tool_call(orch, state, messages, runtime, tool_ctx, decision,
                                      tool_name, args, step_no, llm_start, recorder)
            if sig == "pending":
                if self._session_messages is not None:
                    orch.session_messages = messages
                return self._build_wait_response(
                    orch, state, messages, disclosures_before or [], step_base=step_no,
                )

        # Propagate session messages on non-finish exits too
        if self._session_messages is not None:
            messages.append(LLMMessage("user",
                f"[turn:{self._turn_number}:end] Answer delivered."))
            orch.session_messages = messages

        budget_exhausted = getattr(runtime, "steps_remaining", 1) <= 0
        if orch.run_state.query_result or orch.run_state.answer:
            if orch.run_state.query_result:
                answer = self._answer_from_state(orch)
            else:
                answer = orch.run_state.answer or self._answer_from_state(orch)
            if answer:
                resp = self._build_response(orch, answer, disclosures_before or [])
                if budget_exhausted:
                    logger.warning("loop_budget_exhausted_partial steps=%d", len(state.calls))
                    resp.warnings = list(resp.warnings or []) + [
                        "Step budget exhausted before the task finished — this result may be partial."
                    ]
                return resp

        logger.warning("loop_budget_exhausted steps=%d", len(state.calls))
        return self._build_failed_response(orch, "step_budget_exhausted", disclosures_before or [])

    def _batch_calls(self, decision: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        raw = decision.get("calls")
        if not isinstance(raw, list):
            return [], []
        runnable: list[dict[str, Any]] = []
        dropped: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool:
                continue
            if tool in BATCHABLE_TOOLS and tool in self.allowed_tool_names:
                if len(runnable) < getattr(self.orchestrator.session, "max_batch_tools", _DEFAULT_MAX_BATCH):
                    args = item.get("args") if isinstance(item.get("args"), dict) else {}
                    runnable.append({"tool": tool, "args": args})
            elif tool not in dropped:
                dropped.append(tool)
        return runnable, dropped

    def _run_tool_call(self, orch: AskOrchestrator, state: LoopState, messages: list[LLMMessage],
                       runtime: AgentRuntime, tool_ctx: ToolContext, decision: dict[str, Any],
                       tool_name: str, args: dict[str, Any], step_no: int,
                       llm_start: int, recorder: Any) -> str:
        """Run one tool: emit trace, invoke, append result to messages. Returns
        'pending' (tool paused for the user) or 'ok'."""
        self.progress(self._ns_step(progress_event(
            stage=tool_name, title=f"Calling {tool_name}", status="running", kind="tool",
            detail=str(args)[:200] if args else "", step=step_no,
        )))
        orch.run_state.trace_node = (f"{self._trace_parent}:step:{step_no}"
                                     if self._trace_parent else f"step:{step_no}")
        with llm_stage(tool_name):
            result = runtime.call_tool(tool_name, args, tool_ctx)

        try:
            formatted = _format_tool_result(tool_name, result)
        except Exception:
            formatted = f"ERROR: {result.error.message}" if result.error else "(format error)"
        brief = brief_tool_summary(tool_name, result)
        state.calls.append(ToolCallRecord(tool=tool_name, args=args, ok=result.ok, summary=brief))

        # Append tool result to conversation stream
        messages.append(LLMMessage("user", f"[Tool result: {tool_name}]\n{formatted}"))

        # Circuit-breaker for stuck loops
        if not result.ok:
            escalation = _inject_stuck_loop_hint(state, messages)
            if escalation == "escalate":
                # Leave only 1 step so the model must finish on next iteration
                runtime.force_remaining(1)

        done_detail = brief
        executed_sql = _executed_sql(tool_name, orch, result)
        if executed_sql:
            done_detail = executed_sql
        done_event = self._ns_step(progress_event(
            stage=tool_name, title=f"{tool_name} done",
            status="completed" if result.ok else "failed", kind="tool", detail=done_detail,
            duration_ms=float(getattr(result, "duration_ms", 0) or 0), step=step_no,
        ))
        if args:
            done_event["args"] = args
        if brief and brief != done_detail:
            done_event["output"] = brief
        if recorder is not None:
            iter_calls = recorder.since(llm_start)
            if iter_calls:
                done_event["llm_calls"] = iter_calls
        if isinstance(result.data, dict):
            done_event["result_data"] = result.data
        if executed_sql:
            done_event["sql"] = executed_sql
            data = result.data if isinstance(result.data, dict) else {}
            if data.get("purpose"):
                done_event["purpose"] = str(data["purpose"])
            if data.get("row_count") is not None:
                done_event["row_count"] = data.get("row_count")
            db = str(data.get("database") or orch.run_state.database or "").strip()
            if db:
                done_event["database"] = db
        self.progress(done_event)
        if result.ok and isinstance(result.data, dict) and result.data.get("pending"):
            return "pending"
        return "ok"

    def _tool_call_schemas(self) -> list[dict[str, Any]]:
        if self._tool_schemas_cache is None:
            self._tool_schemas_cache = [_tool_spec_to_function(s) for s in self.allowed_tool_specs]
        return self._tool_schemas_cache

    def _native_decide(self, messages: list[LLMMessage]) -> dict[str, Any] | None:
        """One native tool-calling round → the internal decision dict (same shape
        the JSON protocol returns), or None to fall back to the JSON protocol."""
        orch = self.orchestrator
        if orch.cancel_check:
            orch.cancel_check()
        result = orch.llm.complete_with_tools(messages, self._tool_call_schemas())
        if orch.cancel_check:
            orch.cancel_check()
        tool_calls = result.get("tool_calls") or []
        content = result.get("content")
        if tool_calls:
            calls = [
                {"tool": tc["name"], "args": tc.get("arguments") or {}}
                for tc in tool_calls if tc.get("name") in self.allowed_tool_names
            ]
            if not calls:
                return None  # model named only unknown tools → let JSON protocol retry
            # Many providers return the model's reasoning in `content` alongside the
            # tool call. Keep it as the decision's thought so the native path has the
            # same trace/thought_trace fidelity as the JSON protocol (which carries a
            # "thought" field) instead of always showing a blank "Agent decision".
            thought = (content or "").strip()
            if len(calls) == 1:
                return {"action": "call_tool", "tool": calls[0]["tool"], "args": calls[0]["args"], "thought": thought}
            return {"action": "call_tools", "calls": calls, "thought": thought}
        if content and content.strip():
            # No tool call + content = the model is done → finish. Emit as one chunk
            # so the UI still receives the answer when streaming is on.
            if getattr(orch, "stream_answers", False):
                self._emit_answer_chunk(content)
            return {"action": "finish", "answer": content}
        return None  # neither a tool call nor content → fall back

    def _decide(self, messages: list[LLMMessage]) -> dict[str, Any]:
        """Send the full conversation to the LLM and parse a JSON decision."""
        self._last_prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        orch = self.orchestrator

        # Native function/tool-calling path (capability-gated). The provider emits
        # well-formed tool calls, which we translate into the SAME internal decision
        # dict the JSON protocol produces — so the rest of the loop, compression and
        # resume are unchanged. On an endpoint that rejects tools we downgrade once
        # and use the JSON protocol for the rest of the run.
        if (not self._tools_downgraded
                and getattr(orch, "llm", None) is not None
                and orch.llm.supports_tool_calling()):
            try:
                native = self._native_decide(messages)
                if native is not None:
                    return native
            except _ToolsUnsupported as exc:
                self._tools_downgraded = True
                try:
                    orch.llm._tools_unsupported = True  # cache across runs in-process
                except Exception:
                    pass
                logger.info("native_tool_calling_downgrade: %s", exc)
            except Exception as exc:
                if _looks_cancelled(exc):
                    raise
                self._tools_downgraded = True
                logger.warning("native_tool_calling_failed, using JSON protocol: %s", exc)

        schema_hint = ('Return {"action":"call_tool|call_tools|finish","tool":"...","args":{},'
                       '"calls":[{"tool":"...","args":{}}],"thought":"...","answer":"..."}')

        last_error = ""
        last_raw = ""
        for attempt in range(DECISION_RETRIES):
            call_messages = list(messages)
            if last_error:
                call_messages.append(LLMMessage("user", f"Previous decision invalid: {last_error}. Try again."))
            try:
                if orch.cancel_check:
                    orch.cancel_check()
                if getattr(orch, "stream_answers", False) and orch.llm.supports_streaming():
                    streamer = JsonFieldStreamer(self._emit_answer_chunk, field="answer")
                    def on_chunk(chunk: str) -> None:
                        if orch.cancel_check:
                            orch.cancel_check()
                        streamer.feed(chunk)
                    payload = orch.llm.complete_json_stream(
                        call_messages, schema_hint=schema_hint, on_text_chunk=on_chunk
                    )
                else:
                    payload = orch.llm.complete_json(call_messages, schema_hint=schema_hint)
                if orch.cancel_check:
                    orch.cancel_check()
            except ValueError as exc:
                last_error = f"response was not valid JSON ({exc}); return ONLY a JSON object"
                last_raw = str(exc)[:300]
                logger.warning("loop_decide_parse_failed: %s", exc)
                continue
            except Exception as exc:
                if _looks_cancelled(exc):
                    raise
                last_error = f"LLM call failed ({type(exc).__name__}: {exc}); retry the decision"
                logger.warning("loop_decide_call_failed: %s", exc)
                continue
            last_raw = json.dumps(payload, ensure_ascii=False, default=str)[:500] if payload else ""
            if not isinstance(payload, dict) or not payload.get("action"):
                last_error = "missing action field"
                continue
            action = str(payload.get("action")).lower()
            if action == "finish":
                return payload
            if action == "call_tool" and payload.get("tool"):
                return payload
            if action == "call_tools" and isinstance(payload.get("calls"), list) and payload.get("calls"):
                return payload
            if action in self.allowed_tool_names:
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {
                    k: v for k, v in payload.items()
                    if k not in ("action", "tool", "args", "thought",
                                 "memory_updates", "answer")
                }
                return {**payload, "action": "call_tool", "tool": action, "args": args}
            last_error = f"invalid action payload: {payload!r}"
        raise LoopDecisionError(last_error or "no valid decision", raw_payload=last_raw)

    def _maybe_compress(self, orch: AskOrchestrator, messages: list[LLMMessage]) -> None:
        """Compress older messages if approaching context budget.

        Tracks compression state to avoid infinite re-compression of the same
        segment and falls back to hard truncation if LLM compression fails
        repeatedly.
        """
        budget = self._context_budget()
        total_tokens = sum(estimate_tokens(m.content) for m in messages)
        pct = getattr(orch.session, "compress_threshold", _DEFAULT_COMPRESS_THRESHOLD)
        pct = max(50, min(95, int(pct)))
        threshold = int(budget * pct / 100)
        if total_tokens <= threshold:
            return
        if len(messages) <= 3:
            return

        # Progressive tail preservation: keep more recent messages on first
        # compression, fewer on re-compression (more aggressive).
        compress_count = getattr(self, "_compress_count", 0)
        if compress_count == 0:
            keep_tail = min(6, len(messages) - 2)
        elif compress_count == 1:
            keep_tail = min(4, len(messages) - 2)
        else:
            keep_tail = min(2, len(messages) - 2)
        compress_end = len(messages) - keep_tail
        if compress_end <= 2:
            return

        to_compress = messages[2:compress_end]
        if not to_compress:
            return

        logger.info("context_compress: %d tokens > %d threshold, compressing %d messages (round %d)",
                    total_tokens, threshold, len(to_compress), compress_count + 1)

        compress_text = "\n\n---\n\n".join(
            f"[{m.role}]\n{m.content}" for m in to_compress
        )
        compress_prompt = (
            "Compress the following conversation segment into a structured briefing. "
            "This briefing will replace the original messages so the agent can continue "
            "with full context in fewer tokens.\n\n"
            "CRITICAL — PRESERVE VERBATIM (do NOT abbreviate or omit):\n"
            "- SQL query results: row data, column names, row counts, sample values\n"
            "- SQL query text (the actual SELECT/INSERT/... statements)\n"
            "- Table structures (table names, column names, data types, indexes, FKs)\n"
            "- Verified facts and excluded paths with reasons\n"
            "- User-confirmed criteria and business rules\n"
            "- Join relationships (table.column → ref_table.ref_column)\n"
            "- Error messages from failed tool calls\n\n"
            "COMPRESS AGGRESSIVELY (these are expendable):\n"
            "- Tool call/response formatting overhead (JSON wrappers, metadata)\n"
            "- Intermediate reasoning steps that led to verified conclusions\n"
            "- Redundant schema descriptions (keep only the final/correct version)\n"
            "- Verbose raw JSON payloads (extract key facts only)\n\n"
            "Format as a dense briefing with sections:\n"
            "[Schema] table(col1 type, col2 type, ...) ...\n"
            "[Verified] fact1; fact2; ...\n"
            "[Excluded] path1: reason; ...\n"
            "[SQL Results] purpose → N rows, key findings; actual row data ...\n"
            "[Status] what has been accomplished, what remains\n\n"
            "--- CONVERSATION TO COMPRESS ---\n\n"
            f"{compress_text}"
        )
        try:
            summary = orch.llm.complete_text(
                [LLMMessage("system", "You are a precise conversation compressor for a database assistant."),
                 LLMMessage("user", compress_prompt)],
            )
        except Exception as exc:
            logger.warning("context_compress_llm_failed (round %d): %s", compress_count + 1, exc)
            # Fallback: hard truncation — drop the compressible segment entirely,
            # keeping only a brief note about what was lost.
            fallback_note = (
                f"[Context note: {len(to_compress)} earlier messages were dropped due to "
                f"context limit (compression failed). The agent should rely on verified_facts "
                f"and excluded_paths in memory for cross-step knowledge.]"
            )
            messages[2:compress_end] = [LLMMessage("user", fallback_note)]
            self._compress_count = compress_count + 1
            new_tokens = sum(estimate_tokens(m.content) for m in messages)
            logger.info("context_compress_fallback: %d → %d tokens", total_tokens, new_tokens)
            return

        summary_msg = LLMMessage("user",
            f"[Context summary — compressed from {len(to_compress)} earlier messages]\n{summary}"
        )
        messages[2:compress_end] = [summary_msg]
        self._compress_count = compress_count + 1
        new_tokens = sum(estimate_tokens(m.content) for m in messages)
        logger.info("context_compress_done: %d → %d tokens (round %d)",
                    total_tokens, new_tokens, self._compress_count)

        # Convergence guard: if compression didn't reduce tokens by at least 10%,
        # further LLM compressions are unlikely to help — fall back to hard truncation.
        if new_tokens > total_tokens * 0.9 and new_tokens > threshold:
            logger.warning("context_compress_stall: compression reduced only %d → %d tokens, "
                           "falling back to hard truncation", total_tokens, new_tokens)
            token_sizes = [estimate_tokens(m.content) for m in messages]
            overhead = estimate_tokens("[Context note: earlier messages truncated to fit context budget.]")
            first_keep = _max_tail_keep_index(token_sizes, threshold, head=2, overhead=overhead)
            if first_keep > 2:
                messages[2:first_keep] = [LLMMessage("user",
                    "[Context note: earlier messages truncated to fit context budget.]")]

    @staticmethod
    def _count_completed_turns(messages: list[LLMMessage]) -> int:
        return sum(1 for m in messages if m.role == "user"
                   and m.content.startswith("[turn:") and ":end]" in m.content[:30])

    @staticmethod
    def _find_turn_ranges(messages: list[LLMMessage]) -> list[tuple[int, int]]:
        """Return [(start_idx, end_idx), ...] for each completed turn."""
        ranges: list[tuple[int, int]] = []
        current_start: int | None = None
        for i, m in enumerate(messages):
            if m.role != "user":
                continue
            c = m.content
            if c.startswith("[turn:") and ":start]" in c[:30]:
                current_start = i
            elif c.startswith("[turn:") and ":end]" in c[:30]:
                if current_start is not None:
                    ranges.append((current_start, i))
                    current_start = None
        return ranges

    def _maybe_compress_turns(self, orch: AskOrchestrator, messages: list[LLMMessage]) -> None:
        """Session-aware compression: three-layer message lifecycle.

        Triggered by TOKEN COUNT, not turn count.  When the estimated total
        tokens in ``messages`` exceeds ``compress_threshold`` percent of the
        context budget, the oldest completed turns are compressed while the
        most recent ``session_uncompressed_turns`` completed turns (and the
        in-progress current turn) are always kept raw.

        Layer 1 — Current turn + recent N turns: always raw.
        Layer 2 — Older completed turns: LLM extracts key info into structured
                  JSON, replacing raw messages with a single compact record.
        Layer 3 — Very old turns: if Layer 2 JSON still exceeds budget, oldest
                  compressed turns are further trimmed to a deterministic
                  header built from session.turns[] data.
        """
        budget = self._context_budget()
        # Trigger on the LARGER of the char-heuristic estimate and the exact
        # prompt-token count the API reported for the last call. The estimate
        # drifts (CJK/JSON), and an UNDER-estimate is the dangerous case — it
        # would let the stream grow past the real context window before we
        # compact. The exact count from orch.llm.last_usage is a hard floor that
        # prevents that overflow. (Post-compaction recomputes below stay on the
        # estimate, since last_usage is stale once we rewrite messages.)
        total_tokens = max(
            sum(estimate_tokens(m.content) for m in messages),
            self._reported_prompt_tokens(orch),
        )
        pct = getattr(orch.session, "compress_threshold", _DEFAULT_COMPRESS_THRESHOLD)
        threshold = int(budget * max(50, min(95, int(pct))) / 100)
        if total_tokens <= threshold:
            return

        turn_ranges = self._find_turn_ranges(messages)
        keep_recent = getattr(orch.session, "session_uncompressed_turns", 2)
        compressible = turn_ranges[:-keep_recent] if len(turn_ranges) > keep_recent else []

        # Phase 1: compress raw turns → structured JSON (Layer 2)
        raw_turns = [r for r in compressible if not self._is_already_compressed(messages, r)]
        if raw_turns:
            self._compress_raw_turns(orch, messages, raw_turns)
            total_tokens = sum(estimate_tokens(m.content) for m in messages)
            logger.info("session_compress_phase1_done: %d tokens, threshold %d",
                        total_tokens, threshold)
            if total_tokens <= threshold:
                return

        # Phase 2: demote oldest compressed turns → deterministic header (Layer 3)
        # Scan messages directly for [Compressed turn tN] records — don't use
        # _find_turn_ranges which only finds raw [turn:N:start/end] markers.
        compressed_indices = self._find_compressed_turn_indices(messages)
        if compressed_indices:
            self._demote_compressed_turns(orch, messages, compressed_indices, threshold)

        # Phase 3 backstop: if STILL over threshold — e.g. nothing was compressible
        # because every turn sits within keep_recent, or compression didn't shrink
        # enough — hard-truncate the oldest middle messages so the stream always
        # converges. Mirrors the per-turn path's truncation guard. Runs even when
        # Phase 1/2 had no work to do (that gap previously left an oversized stream).
        self._hard_truncate_session(messages, threshold)

    @staticmethod
    def _hard_truncate_session(messages: list[LLMMessage], threshold: int) -> None:
        """Last-resort convergence guard for the session message stream: drop the
        oldest middle messages, keeping the system prompt (index 0) and the
        largest recent tail that fits — normally including the whole current
        in-progress turn (only a single message larger than the budget on its own
        would be trimmed). Dropped history stays reachable via retrieve_turn /
        list_earlier_turns."""
        total_tokens = sum(estimate_tokens(m.content) for m in messages)
        if total_tokens <= threshold or len(messages) <= 3:
            return
        note = ("[Context note: earlier turns truncated to fit context budget. "
                "Use retrieve_turn / list_earlier_turns for full history.]")
        token_sizes = [estimate_tokens(m.content) for m in messages]
        overhead = estimate_tokens(note)
        cumulative = 0
        first_keep = 1
        for i in range(len(messages) - 1, 0, -1):
            cumulative += token_sizes[i]
            if token_sizes[0] + overhead + cumulative > threshold:
                first_keep = i + 1
                break
        if first_keep > 1:
            # Pin the current turn's [turn:N:start] message even if it falls in the
            # dropped range: it carries the re-injected [Confirmed criteria] / pinned
            # scope that session_turn_prompt promised the model would honor this turn.
            replacement: list[LLMMessage] = [LLMMessage("user", note)]
            start_idx = None
            for i in range(len(messages) - 1, 0, -1):
                head = messages[i].content.split("\n", 1)[0]
                if head.startswith("[turn:") and ":start]" in head:
                    start_idx = i
                    break
            if start_idx is not None and 1 <= start_idx < first_keep:
                replacement.append(messages[start_idx])
            messages[1:first_keep] = replacement
            logger.warning("session_compress_hard_truncate: dropped messages[1:%d], "
                           "%d → %d tokens", first_keep, total_tokens,
                           sum(estimate_tokens(m.content) for m in messages))

    @staticmethod
    def _is_already_compressed(messages: list[LLMMessage], turn_range: tuple[int, int]) -> bool:
        start, end = turn_range
        return start == end and messages[start].content.startswith("[Compressed turn t")

    @staticmethod
    def _find_compressed_turn_indices(messages: list[LLMMessage]) -> list[int]:
        """Return indices of already-compressed turn messages (oldest first)."""
        return [
            i for i, m in enumerate(messages)
            if m.role == "user" and m.content.startswith("[Compressed turn t")
        ]

    def _compress_raw_turns(
        self, orch: AskOrchestrator, messages: list[LLMMessage],
        turn_ranges: list[tuple[int, int]],
    ) -> None:
        """Layer 1→2: LLM-based structured JSON extraction for raw turns."""
        compress_failures = 0
        for start_idx, end_idx in reversed(turn_ranges):
            to_compress = messages[start_idx:end_idx + 1]
            turn_num = self._extract_turn_number(to_compress)

            try:
                summary = self._llm_extract_turn_summary(orch, to_compress, turn_num)
                messages[start_idx:end_idx + 1] = [LLMMessage("user",
                    f"[Compressed turn t{turn_num} — retrieve_turn(t{turn_num}) for full details]\n"
                    f"{summary}")]
                logger.info("session_compress_turn: t%d, %d msgs → summary", turn_num, len(to_compress))
            except Exception as exc:
                compress_failures += 1
                logger.warning("session_compress_turn_failed: t%d: %s", turn_num, exc)
                fallback = self._fallback_turn_summary(orch, to_compress, turn_num)
                messages[start_idx:end_idx + 1] = [LLMMessage("user", fallback)]
                if compress_failures >= 3:
                    logger.warning("session_compress_circuit_break: %d consecutive failures", compress_failures)
                    break

    def _llm_extract_turn_summary(
        self, orch: AskOrchestrator, turn_msgs: list[LLMMessage], turn_num: int,
    ) -> str:
        """Ask the LLM for a DENSE FREE-TEXT briefing of this turn (not strict JSON).

        The summary is re-inserted into the conversation stream and read by the next
        model call, so a free-text sectioned briefing is exactly as useful as JSON
        for that consumer — and it removes the rigid-format burden plus the
        json.loads parse-failure path (a free-text reply can never "fail to parse",
        only the genuine LLM/transport error path remains, handled by the caller's
        deterministic fallback)."""
        compress_text = "\n\n---\n\n".join(
            f"[{m.role}]\n{m.content}" for m in turn_msgs
        )
        # Surface the turn's structured criteria / verified facts / ruled-out paths
        # to the extractor — they live in run_state, not always verbatim in the
        # messages, but MUST survive into the summary so later turns can rely on
        # history (rather than every-turn force-injection) for them.
        td = self._find_session_turn(orch, turn_num) or {}
        extra: list[str] = []
        if td.get("clarifications"):
            extra.append("Confirmed criteria: " + "; ".join(str(c) for c in td["clarifications"]))
        if td.get("verified_facts"):
            extra.append("Verified facts: " + "; ".join(str(f) for f in td["verified_facts"]))
        if td.get("excluded_paths"):
            extra.append("Ruled-out: " + "; ".join(
                f"{e.get('target', '')}: {e.get('reason', '')}"
                for e in td["excluded_paths"] if isinstance(e, dict) and e.get("target")
            ))
        if extra:
            compress_text += (
                "\n\n---\n\n[STRUCTURED CONTEXT — preserve these verbatim under the "
                "matching sections]\n" + "\n".join(extra)
            )
        prompt = (
            "Compress this database-exploration turn into a DENSE briefing that REPLACES\n"
            "the raw messages — the agent must be able to continue using only this.\n"
            "Use these sections (omit a section if it has nothing):\n"
            "[Question] the user's question this turn\n"
            "[Tables] each table: name, key columns + types, FK/index/enum notes\n"
            "[SQL] every executed SQL with its purpose and the key result rows/numbers "
            "(preserve actual values — follow-ups need them to avoid re-running)\n"
            "[Criteria] user-confirmed conditions/filters/business rules\n"
            "[Discoveries] schema/data insights not obvious from table names\n"
            "[Excluded] approaches tried and rejected, with the reason\n"
            "[Answer] the concise final answer delivered\n\n"
            "DISCARD: tool-call/JSON wrappers, validate_sql confirmations, metadata,\n"
            "intermediate reasoning. Keep facts, SQL, results, schema, criteria.\n\n"
            f"--- TURN {turn_num} TO COMPRESS ---\n\n{compress_text}"
        )
        result = orch.llm.complete_text(
            [LLMMessage("system",
                "You are a precise conversation compressor for a database assistant. "
                "Output ONLY the dense briefing, no preamble."),
             LLMMessage("user", prompt)],
        )
        text = result.strip()
        if not text:
            raise ValueError("empty compression summary")
        return text

    def _fallback_turn_summary(
        self, orch: AskOrchestrator, turn_msgs: list[LLMMessage], turn_num: int,
    ) -> str:
        """Deterministic fallback: build a compressed record from session.turns[] data."""
        turn_data = self._find_session_turn(orch, turn_num)
        if turn_data:
            record: dict[str, Any] = {"question": turn_data.get("question", "")}
            tables = turn_data.get("disclosed_tables") or []
            if tables:
                record["tables"] = tables
            exec_sqls = turn_data.get("executed_sqls") or []
            if exec_sqls:
                record["executed_sqls"] = [
                    {"sql": e.get("sql", ""), "purpose": e.get("purpose", ""),
                     "row_count": e.get("row_count", 0)}
                    for e in exec_sqls if isinstance(e, dict) and e.get("sql")
                ]
            elif turn_data.get("selected_sql"):
                record["executed_sqls"] = [{"sql": turn_data["selected_sql"]}]
            criteria = turn_data.get("clarifications") or []
            if criteria:
                record["criteria"] = criteria
            # Preserve verified facts + ruled-out paths in the summary so they
            # survive in history (the model attends to them by relevance on later
            # turns) instead of being force-injected into every turn's prompt.
            facts = turn_data.get("verified_facts") or []
            if facts:
                record["discoveries"] = list(facts)
            excluded = turn_data.get("excluded_paths") or []
            if excluded:
                record["excluded"] = [
                    f"{e.get('target', '')}: {e.get('reason', '')}".strip(": ")
                    for e in excluded if isinstance(e, dict) and e.get("target")
                ]
            answer = (turn_data.get("answer_markdown") or "")[:300]
            if answer:
                record["answer"] = answer
            body = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            return (
                f"[Compressed turn t{turn_num} — retrieve_turn(t{turn_num}) for full details]\n"
                f"{body}"
            )
        q_line = ""
        for m in turn_msgs:
            if m.content.startswith("[turn:") and ":start]" in m.content[:30]:
                lines = m.content.split("\n", 3)
                q_line = lines[1] if len(lines) > 1 else ""
                break
        record = {"question": q_line, "answer": "(compression failed — use retrieve_turn)"}
        return (
            f"[Compressed turn t{turn_num} — retrieve_turn(t{turn_num}) for full details]\n"
            + json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        )

    def _demote_compressed_turns(
        self, orch: AskOrchestrator, messages: list[LLMMessage],
        compressed_indices: list[int], threshold: int,
    ) -> None:
        """Layer 2→3: trim oldest compressed turns to minimal deterministic headers."""
        for idx in compressed_indices:
            msg = messages[idx]
            turn_num = self._extract_turn_number_from_compressed(msg.content)
            if turn_num == 0:
                continue
            turn_data = self._find_session_turn(orch, turn_num)
            q = (turn_data.get("question", "") if turn_data else "")[:100]
            tables = (turn_data.get("disclosed_tables") or [])[:5] if turn_data else []
            record: dict[str, Any] = {"question": q}
            if tables:
                record["tables"] = tables
            body = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            messages[idx] = LLMMessage("user",
                f"[Compressed turn t{turn_num} — retrieve_turn(t{turn_num}) for full details]\n"
                f"{body}")
            total = sum(estimate_tokens(m.content) for m in messages)
            if total <= threshold:
                break

    @staticmethod
    def _extract_turn_number(turn_msgs: list[LLMMessage]) -> int:
        import re
        for m in turn_msgs:
            if m.content.startswith("[turn:") and ":start]" in m.content[:30]:
                match = re.match(r"\[turn:(\d+):start\]", m.content)
                if match:
                    return int(match.group(1))
        return 0

    @staticmethod
    def _extract_turn_number_from_compressed(content: str) -> int:
        import re
        match = re.match(r"\[Compressed turn t(\d+)", content)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _find_session_turn(orch: AskOrchestrator, turn_num: int) -> dict[str, Any] | None:
        turns = getattr(orch, "session_turns", []) or []
        if 0 < turn_num <= len(turns):
            return turns[turn_num - 1]
        return None

    def _build_failed_response(
        self, orch: AskOrchestrator, reason: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        orch.run_state.fail_reason = reason
        self.progress(
            progress_event(
                stage="loop",
                title="Agent loop",
                status="failed",
                kind="phase",
                detail=reason,
                node_id=self._agent_loop_node_id or "",
                parent_id=self._trace_parent,
            ),
        )
        from dbaide.i18n import t
        answer = (orch.run_state.answer or "").strip()
        if not answer and orch.run_state.query_result:
            answer = self._answer_from_state(orch)
        if not answer:
            answer = t("agent.loop_failed")
        selected_sql, executed_sqls = response_sql_exports(orch.run_state)
        return AssistantResponse(
            answer=answer,
            sql=selected_sql,
            result=orch.run_state.query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[t("agent.loop_failed_reason", reason=reason)],
            charts=list(orch.run_state.charts or []),
            executed_sqls=executed_sqls,
        )

    def _emit_answer_chunk(self, text: str) -> None:
        if text:
            self.progress({"kind": "answer_chunk", "text": text})

    def _answer_from_state(self, orch: AskOrchestrator) -> str:
        if orch.run_state.query_result and orch.run_state.sql:
            draft_rationale = orch.run_state.sql_rationale or ""
            interpretation = orch.interpreter.interpret(
                question=orch.run_state.question or "",
                sql=orch.run_state.sql,
                row_count=orch.run_state.query_result.row_count,
                columns=orch.run_state.query_result.columns,
                elapsed_ms=orch.run_state.query_result.elapsed_ms,
                truncated=orch.run_state.query_result.truncated,
                warnings=[],
                language=orch.run_state.answer_language,
            )
            return orch.formatter.query_result(
                orch.run_state.query_result,
                rationale=draft_rationale,
                interpretation=interpretation,
                language=orch.run_state.answer_language,
            )
        if orch.run_state.sql and not orch.run_state.query_result:
            note = "已生成 SQL（未执行）。" if orch.run_state.answer_language == "zh" else "SQL generated (not executed)."
            return f"SQL:\n```sql\n{orch.run_state.sql}\n```\n\n_{note}_"
        return orch.run_state.answer or ""

    def _build_wait_response(
        self,
        orch: AskOrchestrator,
        state: LoopState,
        messages: list[LLMMessage],
        disclosures_before: list[str],
        *,
        step_base: int = 0,
    ) -> AssistantResponse:
        question = orch.run_state.pending_question
        options = list(orch.run_state.pending_options)
        lines = [question]
        if options:
            lines.append("")
            lines.append("Options:")
            lines.extend(f"- {item}" for item in options)
        answer = "\n".join(lines)
        snapshot = dump_loop_state(orch, messages=messages, execute_allowed=state.execute_allowed)
        snapshot["step_base"] = int(step_base)
        ask_step_id = (f"{self._trace_parent}:step:{step_base}"
                       if self._trace_parent else f"step:{step_base}")
        wait_event = progress_event(
            stage="ask_user",
            title="Waiting for user clarification",
            status="waiting",
            kind="user",
            detail=question,
            node_id=f"{ask_step_id}:waiting",
            parent_id=ask_step_id,
        )
        wait_event["question"] = question
        if options:
            wait_event["options"] = options
        structured = list(orch.run_state.pending_questions)
        if structured:
            wait_event["questions"] = structured
        self.progress(wait_event)
        selected_sql, executed_sqls = response_sql_exports(orch.run_state)
        return AssistantResponse(
            answer=answer,
            sql=selected_sql,
            result=None,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
            status="wait_user",
            pending_question=question,
            pending_options=options,
            pending_questions=list(orch.run_state.pending_questions),
            resume_state=snapshot,
            charts=list(orch.run_state.charts or []),
            executed_sqls=executed_sqls,
        )

    def _build_response(
        self, orch: AskOrchestrator, answer: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        selected_sql, executed_sqls = response_sql_exports(orch.run_state)
        return AssistantResponse(
            answer=answer,
            sql=selected_sql,
            result=orch.run_state.query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
            charts=list(orch.run_state.charts or []),
            executed_sqls=executed_sqls,
        )

    def _trace_sink(self, event: TraceEvent) -> None:
        if event.actor in {"tool", "runtime"}:
            return
        self.progress(from_trace_event(event))


# ── Tool result formatting ───────────────────────────────────────────

_TOOL_FORMATTERS: dict[str, str] = {
    "execute_sql": "_sql_result",
    "describe_table": "_describe",
    "retrieve_schema_context": "_schema",
    "discover_schema": "_schema",
    "column_stats": "_profile",
    "profile_table": "_profile",
    "validate_joins": "_join",
    "retrieve_join_context": "_join",
    "generate_sql": "_generate_sql",
    "validate_sql": "_validate_sql",
    "explain_sql": "_explain_sql",
    "list_tables": "_list_items",
    "list_databases": "_list_items",
    "ask_user": "_ask_user",
    "run_subagent": "_subagent",
}


def _format_tool_result(tool: str, result: ToolResult) -> str:
    """Smart per-tool formatting for conversation messages.

    Unlike blind json.dumps[:1400] truncation, this preserves structure
    and key information while keeping the result within a reasonable size.
    """
    if not result.ok:
        if result.error:
            return f"ERROR: {result.error.message}"
        return "FAILED (no details)"
    data = result.data
    if data is None:
        return "ok (empty)"

    formatter_key = _TOOL_FORMATTERS.get(tool)
    if formatter_key:
        fn = globals().get(f"_fmt{formatter_key}")
        if fn:
            return fn(data)
    return _fmt_generic(data)


def _fmt_generic(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > _RESULT_SOFT_CAP:
        return text[:_RESULT_SOFT_CAP] + f"\n…[truncated from {len(text)} chars]"
    return text


def _fmt_sql_result(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("sql"):
        parts.append(f"SQL: {data['sql']}")
    if data.get("pending") or data.get("blocked"):
        parts.append(json.dumps(data, ensure_ascii=False, default=str))
        return "\n".join(parts)
    row_count = data.get("row_count")
    if row_count is not None:
        truncated = " (TRUNCATED — more rows exist)" if data.get("truncated") else ""
        parts.append(f"Rows: {row_count}{truncated}")
    columns = data.get("columns")
    if columns:
        parts.append(f"Columns: {', '.join(str(c) for c in columns)}")
    rows = data.get("rows")
    if isinstance(rows, list) and rows:
        row_meta = data.get("row_preview") if isinstance(data.get("row_preview"), dict) else {}
        preview = rows
        if not row_meta:
            preview, row_meta = preview_rows(
                rows,
                columns=data.get("columns") if isinstance(data.get("columns"), list) else None,
                max_rows=20,
            )
        rows_text = json.dumps(preview, ensure_ascii=False, default=str)
        cell_cap = row_meta.get("max_cell_chars", 500)
        parts.append(f"Data (first {len(preview)} row(s), cells capped at {cell_cap} chars):\n{rows_text}")
        notes = []
        if row_meta.get("row_preview_truncated"):
            notes.append(f"showing {row_meta.get('rows_previewed')} of {row_meta.get('rows_returned')} returned rows")
        if row_meta.get("cell_truncated"):
            notes.append(f"{row_meta.get('truncated_cells')} cell(s) truncated")
        if notes:
            parts.append("(" + "; ".join(notes) + ")")
    for key in ("artifact_id", "purpose", "warnings", "elapsed_ms", "fast_executed"):
        if data.get(key) is not None:
            parts.append(f"{key}: {data[key]}")
    return "\n".join(parts)


def _fmt_describe(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    db = data.get("database", "")
    table = data.get("table", "")
    parts.append(f"Table: {db}.{table}" if db else f"Table: {table}")
    columns = data.get("columns")
    if isinstance(columns, list):
        col_lines = []
        for col in columns:
            if isinstance(col, dict):
                name = col.get("name", "")
                dtype = col.get("data_type", "")
                pk = " PK" if col.get("primary_key") else ""
                idx = " IDX" if col.get("indexed") else ""
                nullable = " NULL" if col.get("nullable") else ""
                comment = f" -- {col['comment']}" if col.get("comment") else ""
                note = f" [NOTE: {col['note']}]" if col.get("note") else ""
                col_lines.append(f"  {name} {dtype}{pk}{idx}{nullable}{comment}{note}")
        parts.append(f"Columns ({len(columns)}):\n" + "\n".join(col_lines))
    if data.get("indexes"):
        parts.append(f"Indexes: {json.dumps(data['indexes'], ensure_ascii=False, default=str)}")
    if data.get("foreign_keys"):
        parts.append(f"Foreign keys: {json.dumps(data['foreign_keys'], ensure_ascii=False, default=str)}")
    if data.get("row_count") is not None:
        parts.append(f"Estimated rows: {data['row_count']}")
    return "\n".join(parts)


def _fmt_schema(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("source_summary"):
        parts.append(f"Summary: {data['source_summary']}")
    candidates = data.get("candidates")
    if isinstance(candidates, list):
        for c in candidates:
            if isinstance(c, dict):
                label = f"{c.get('database', '')}.{c.get('table', '')}" if c.get("database") else c.get("table", "?")
                status = c.get("status", "active")
                cols = c.get("columns", [])
                col_str = ", ".join(str(col.get("name", "")) for col in (cols[:12] if isinstance(cols, list) else []))
                if isinstance(cols, list) and len(cols) > 12:
                    col_str += f" …+{len(cols) - 12}"
                parts.append(f"  {label} ({status}): [{col_str}]")
    if data.get("missing"):
        parts.append(f"Missing: {data['missing']}")
    if data.get("instruction"):
        parts.append(f"Note: {data['instruction']}")
    text = "\n".join(parts)
    if not text:
        return _fmt_generic(data)
    if len(text) > _RESULT_SOFT_CAP:
        return text[:_RESULT_SOFT_CAP] + f"\n…[truncated from {len(text)} chars]"
    return text


def _fmt_profile(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("table"):
        parts.append(f"Table: {data.get('database', '')}.{data['table']}" if data.get("database") else f"Table: {data['table']}")
    if data.get("row_count") is not None:
        parts.append(f"Row count: {data['row_count']}")
    stats = data.get("columns") or data.get("profiles") or data.get("column_stats") or data.get("stats")
    if isinstance(stats, list):
        for s in stats[:20]:
            if isinstance(s, dict):
                name = s.get("column") or s.get("name", "?")
                metric_source = s.get("stats") if isinstance(s.get("stats"), dict) else s
                metric_bits = _metric_bits(metric_source)
                if metric_bits:
                    parts.append(f"  {name}: " + ", ".join(metric_bits))
                else:
                    parts.append(f"  {name}: {json.dumps(s, ensure_ascii=False, default=str)}")
        if len(stats) > 20:
            parts.append(f"  …+{len(stats) - 20} columns")
    text = "\n".join(parts)
    if not text:
        return _fmt_generic(data)
    return text


def _metric_bits(stats: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    aliases = {
        "null_count": "nulls",
        "distinct_count": "distinct",
        "min_value": "min",
        "max_value": "max",
    }
    for key in (
        "null_count", "distinct_count", "min_value", "max_value",
        "null_rate", "empty_rate", "min", "max", "min_len", "max_len",
    ):
        if key in stats and stats.get(key) is not None:
            bits.append(f"{aliases.get(key, key)}={stats.get(key)}")
    top_values = stats.get("top_values")
    if isinstance(top_values, list) and top_values:
        preview = top_values[:5]
        bits.append("top_values=" + json.dumps(preview, ensure_ascii=False, default=str))
    note = stats.get("note")
    if note:
        bits.append(f"note={note}")
    return bits


def _fmt_join(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("source_summary"):
        parts.append(f"Summary: {data['source_summary']}")
    relations = data.get("relations")
    if isinstance(relations, list):
        for rel in relations[:10]:
            if isinstance(rel, dict):
                src = f"{rel.get('table', '?')}.{rel.get('column', '?')}"
                ref = f"{rel.get('ref_table', '?')}.{rel.get('ref_column', '?')}"
                source = rel.get("source", "?")
                conf = rel.get("confidence")
                conf_str = f" conf={conf}" if conf is not None else ""
                parts.append(f"  {src} → {ref} ({source}{conf_str})")
        if len(relations) > 10:
            parts.append(f"  …+{len(relations) - 10} relations")
    if data.get("warnings"):
        parts.append(f"Warnings: {data['warnings']}")
    if data.get("instruction"):
        parts.append(f"Note: {data['instruction']}")
    text = "\n".join(parts)
    if not text:
        return _fmt_generic(data)
    return text


def _fmt_generate_sql(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("sql"):
        parts.append(f"Generated SQL:\n{data['sql']}")
    if data.get("confidence") is not None:
        parts.append(f"Confidence: {data['confidence']}")
    if data.get("fast_executed"):
        parts.append("Status: fast-executed (result available)")
    if data.get("validation_warnings"):
        parts.append(f"Validation warnings: {data['validation_warnings']}")
    if data.get("rationale"):
        parts.append(f"Rationale: {str(data['rationale'])[:500]}")
    # If fast-executed, include result data inline (skip SQL line to avoid duplication)
    if data.get("rows") is not None or data.get("row_count") is not None:
        result_parts: list[str] = []
        row_count = data.get("row_count")
        if row_count is not None:
            truncated = " (TRUNCATED — more rows exist)" if data.get("truncated") else ""
            result_parts.append(f"Rows: {row_count}{truncated}")
        columns = data.get("columns")
        if columns:
            result_parts.append(f"Columns: {', '.join(str(c) for c in columns)}")
        rows = data.get("rows")
        if isinstance(rows, list) and rows:
            min_rows = min(5, len(rows))
            max_rows = min(20, len(rows))
            rows_text = json.dumps(rows[:max_rows], ensure_ascii=False, default=str)
            if len(rows_text) > 4000 and max_rows > min_rows:
                rows_text = json.dumps(rows[:min_rows], ensure_ascii=False, default=str)
                if len(rows_text) > 4000:
                    rows_text = rows_text[:4000] + "…"
                result_parts.append(f"Data (first {min_rows} of {len(rows)} rows):\n{rows_text}")
            else:
                result_parts.append(f"Data:\n{rows_text}")
        if result_parts:
            parts.append("\n".join(result_parts))
    text = "\n".join(parts)
    return text if text else _fmt_generic(data)


def _fmt_validate_sql(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    ok = data.get("ok")
    parts.append(f"Valid: {'yes' if ok else 'no'}")
    if data.get("normalized_sql"):
        parts.append(f"Normalized SQL: {data['normalized_sql']}")
    issues = data.get("issues")
    if isinstance(issues, list) and issues:
        for issue in issues:
            if isinstance(issue, dict):
                parts.append(f"  Issue: {issue.get('message', '?')} ({issue.get('severity', 'error')})")
            else:
                parts.append(f"  Issue: {issue}")
    if data.get("risk_level"):
        parts.append(f"Risk level: {data['risk_level']}")
    if data.get("warnings"):
        parts.append(f"Warnings: {data['warnings']}")
    if data.get("requires_confirmation"):
        parts.append("Requires user confirmation before execution.")
    return "\n".join(parts)


def _fmt_explain_sql(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("ok") is not None:
        parts.append(f"OK: {data['ok']}")
    plan = data.get("plan") or data.get("explain_plan")
    if plan:
        plan_text = json.dumps(plan, ensure_ascii=False, default=str) if not isinstance(plan, str) else plan
        if len(plan_text) > 3000:
            plan_text = plan_text[:3000] + "…[truncated]"
        parts.append(f"EXPLAIN plan:\n{plan_text}")
    if data.get("issues"):
        parts.append(f"Issues: {data['issues']}")
    if data.get("suggestions"):
        parts.append(f"Suggestions: {data['suggestions']}")
    text = "\n".join(parts)
    return text if text else _fmt_generic(data)


def _fmt_list_items(data: Any) -> str:
    if isinstance(data, dict):
        items = data.get("tables") or data.get("databases") or data.get("items")
        if isinstance(items, list):
            count = len(items)
            preview = items[:30]
            names = [str(x.get("name", x) if isinstance(x, dict) else x) for x in preview]
            result = f"Count: {count}\nItems: {', '.join(names)}"
            if count > 30:
                result += f" …+{count - 30}"
            return result
    return _fmt_generic(data)


def _fmt_ask_user(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("pending"):
        parts.append("Status: waiting for user response")
    if data.get("question"):
        parts.append(f"Question: {data['question']}")
    if data.get("options"):
        parts.append(f"Options: {data['options']}")
    return "\n".join(parts) if parts else _fmt_generic(data)


def _fmt_subagent(data: Any) -> str:
    if not isinstance(data, dict):
        return _fmt_generic(data)
    parts: list[str] = []
    if data.get("task"):
        parts.append(f"Task: {data['task']}")
    if data.get("status"):
        parts.append(f"Status: {data['status']}")
    if data.get("answer"):
        parts.append(f"Answer:\n{data['answer']}")
    if data.get("sql"):
        parts.append(f"SQL:\n{data['sql']}")
    if data.get("result_preview"):
        parts.append("Result preview:\n" + json.dumps(data["result_preview"], ensure_ascii=False, default=str))
        row_meta = data.get("row_preview") if isinstance(data.get("row_preview"), dict) else {}
        notes = []
        if row_meta.get("row_preview_truncated"):
            notes.append(f"showing {row_meta.get('rows_previewed')} of {row_meta.get('rows_returned')} returned rows")
        if row_meta.get("cell_truncated"):
            notes.append(f"{row_meta.get('truncated_cells')} cell(s) truncated")
        if notes:
            parts.append("(" + "; ".join(notes) + ")")
    if data.get("charts"):
        chart_ids = [
            str(item.get("chart_id"))
            for item in data.get("charts")
            if isinstance(item, dict) and item.get("chart_id")
        ]
        if chart_ids:
            parts.append(f"Charts: {', '.join(chart_ids)}")
    if data.get("executed_sqls"):
        executed = data.get("executed_sqls")
        if isinstance(executed, list):
            lines = []
            for item in executed[:5]:
                if isinstance(item, dict) and item.get("sql"):
                    purpose = f" ({item.get('purpose')})" if item.get("purpose") else ""
                    lines.append(f"- {item.get('artifact_id') or item.get('index') or '?'}{purpose}: {item.get('sql')}")
            if lines:
                parts.append("Executed SQL:\n" + "\n".join(lines))
            if len(executed) > 5:
                parts.append(f"…+{len(executed) - 5} executed SQL entries")
    if data.get("pending_question"):
        parts.append(f"Pending question: {data['pending_question']}")
    if data.get("pending_options"):
        parts.append(f"Pending options: {data['pending_options']}")
    if data.get("warnings"):
        parts.append(f"Warnings: {data['warnings']}")
    return "\n\n".join(parts) if parts else _fmt_generic(data)


_SCALAR_JSON_TYPES = {"int": "integer", "integer": "integer", "number": "number",
                      "float": "number", "bool": "boolean", "boolean": "boolean",
                      "object": "object", "dict": "object", "string": "string", "str": "string"}


def _json_prop(value: Any) -> dict[str, Any]:
    """Map a dbaide spec type string to a JSON-Schema property. Handles the spec's
    own forms (``list[string]``, ``list[dict]``, ``dict``) so array/object args are
    advertised correctly to a native tool-calling provider, not flattened to string."""
    t = str(value or "").lower().strip()
    if t.startswith("list[") and t.endswith("]"):
        inner = _SCALAR_JSON_TYPES.get(t[5:-1].strip(), "string")
        return {"type": "array", "items": {"type": inner}}
    if t in ("list", "array"):
        return {"type": "array", "items": {"type": "string"}}
    return {"type": _SCALAR_JSON_TYPES.get(t, "string")}


def _tool_spec_to_function(spec: Any) -> dict[str, Any]:
    """Map a dbaide ToolSpec to an OpenAI function-tool schema for native calling."""
    schema = getattr(spec, "input_schema", None) or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, meta in schema.items():
        if not isinstance(meta, dict):
            properties[key] = {"type": "string"}
            continue
        prop = _json_prop(meta.get("type"))
        desc = meta.get("description")
        if desc:
            prop["description"] = str(desc)
        properties[key] = prop
        if meta.get("required"):
            required.append(key)
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": str(getattr(spec, "description", "") or ""),
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _inject_stuck_loop_hint(state: LoopState, messages: list[LLMMessage]) -> str:
    """Detect and handle stuck loops. Returns 'escalate' if the model should be
    force-finished, empty string otherwise."""
    recent = state.calls
    # Check escalation threshold first (higher priority)
    n_esc = _STUCK_LOOP_ESCALATION
    tail_esc = recent[-n_esc:]
    if len(tail_esc) >= n_esc and all(not r.ok for r in tail_esc) and len({r.tool for r in tail_esc}) == 1:
        logger.warning("stuck_loop_escalation tool=%s repeats=%d", tail_esc[0].tool, n_esc)
        messages.append(LLMMessage("user",
            f"CRITICAL: tool `{tail_esc[0].tool}` has failed {n_esc} consecutive times. "
            f"You MUST call finish NOW with an explanation of the limitation. "
            f"Do not attempt any more tool calls."
        ))
        return "escalate"
    # Check initial hint threshold
    n = _STUCK_LOOP_THRESHOLD
    tail = recent[-n:]
    if len(tail) >= n and all(not r.ok for r in tail) and len({r.tool for r in tail}) == 1 and len({r.summary for r in tail}) == 1:
        logger.warning("stuck_loop_detected tool=%s repeats=%d", tail[0].tool, n)
        messages.append(LLMMessage("user",
            f"WARNING: tool `{tail[0].tool}` has failed {n} consecutive times "
            f"with the same error. This is likely a validation limitation, not "
            f"a fixable SQL issue. DO NOT retry the same approach — either try "
            f"a fundamentally different SQL structure, use a different tool, "
            f"or call finish with an explanation of the limitation."
        ))
    return ""


def _looks_cancelled(exc: Exception) -> bool:
    return isinstance(exc, CancelledError)
