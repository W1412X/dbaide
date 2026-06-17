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
from dbaide.llm import LLMMessage
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


def _executed_sql(tool_name: str, orch, result) -> str:
    if tool_name not in _SQL_TOOLS:
        return ""
    data = getattr(result, "data", None)
    if isinstance(data, dict) and data.get("sql"):
        return str(data["sql"]).strip()
    return str(orch.run_state.sql or "").strip()


def _risk_reply_confirms(reply: str) -> bool:
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
    ) -> AssistantResponse:
        orch = self.orchestrator
        self._trace_parent = trace_parent
        self._agent_loop_node_id = f"{trace_parent}:loop" if trace_parent else "loop"
        messages: list[LLMMessage] = []
        approved_risk_sql = ""
        approved_risk_args: dict[str, Any] = {}
        approved_risk_tool = "execute_sql"

        if resume_state:
            messages, execute = restore_loop_state(orch, resume_state)
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
            # Build initial conversation: system + user question
            tools = self.allowed_tool_specs
            tool_lines = "\n".join(tool_prompt_line(s) for s in tools)
            execute_note = "allowed" if state.execute_allowed else "disabled"
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

        if approved_risk_sql:
            self.progress(
                self._ns_step(progress_event(
                    stage=approved_risk_tool,
                    title="Executing confirmed SQL",
                    status="running",
                    kind="tool",
                    detail=approved_risk_sql[:240],
                    step=1,
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
                step=1,
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

            self._apply_decision_memory(decision)
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
                return self._build_wait_response(
                    orch, state, messages, disclosures_before or [], step_base=step_no,
                )

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

        formatted = _format_tool_result(tool_name, result)
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

    def _apply_decision_memory(self, decision: dict[str, Any]) -> None:
        """Extract cross-run knowledge from model decisions: verified facts and excluded paths."""
        mem = self.orchestrator.run_state.memory
        updates = decision.get("memory_updates") if isinstance(decision.get("memory_updates"), dict) else {}
        for item in _list_update_items(updates.get("verified")) + _list_update_items(updates.get("confirmed")):
            if isinstance(item, dict):
                mem.mark_verified(str(item.get("text") or ""))
            else:
                mem.mark_verified(str(item))
        for item in _list_update_items(updates.get("excluded_paths")) + _list_update_items(updates.get("exclusions")):
            if isinstance(item, dict):
                mem.add_exclusion(
                    str(item.get("target") or ""),
                    str(item.get("reason") or ""),
                    evidence_ref=str(item.get("evidence_ref") or "decision"),
                )

    def _decide(self, messages: list[LLMMessage]) -> dict[str, Any]:
        """Send the full conversation to the LLM and parse a JSON decision."""
        self._last_prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        schema_hint = ('Return {"action":"call_tool|call_tools|finish","tool":"...","args":{},'
                       '"calls":[{"tool":"...","args":{}}],"thought":"...","answer":"..."}')
        orch = self.orchestrator

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
            keep = max(2, len(messages) - 2)
            while sum(estimate_tokens(m.content) for m in messages) > threshold and keep > 2:
                keep -= 1
                messages[2:len(messages) - keep] = [LLMMessage("user",
                    f"[Context note: earlier messages truncated to fit context budget.]")]

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


def _list_update_items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
