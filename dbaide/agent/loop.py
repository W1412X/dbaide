"""LLM tool-calling loop for Ask agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from dbaide.agent.answer_stream import JsonFieldStreamer
from dbaide.agent.loop_state import dump_loop_state, restore_loop_state
from dbaide.agent.progress_events import brief_tool_summary, from_trace_event, progress_event
from dbaide.agent.loop_prompts import DecisionPromptBuilder, tool_prompt_line
from dbaide.agent.llm_trace import llm_stage
from dbaide.agent.runtime import AgentRuntime
from dbaide.agent.toolkit import build_tool_registry, loop_tool_specs
from dbaide.core.cancellation import CancelledError
from dbaide.core.events import TraceEvent
from dbaide.llm import LLMMessage
from dbaide.models import AssistantResponse
from dbaide.tools.registry import ToolContext, ToolResult

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.loop")

DECISION_RETRIES = 1
# Both names bind to the same execute handler; the loop must treat them alike.
_EXECUTE_TOOLS = frozenset({"execute_sql", "execute_readonly_sql"})

# Tools the model may emit several of in ONE decision (action="call_tools"), to cut
# loop round-trips. Deliberately limited to INDEPENDENT, READ-ONLY evidence gathering
# with no ordering dependency, no user pause, and no state mutation. Everything else
# — the generate→validate→execute SQL chain, ask_user, and writes (annotate/joins) —
# stays one-per-decision so its safety gates (risk controller, validation order,
# pause/resume) and the "decide from the result" loop are preserved.
BATCHABLE_TOOLS = frozenset({
    "discover_schema", "retrieve_schema_context", "list_databases", "list_tables",
    "describe_table", "inspect_metadata", "retrieve_join_context",
    "column_stats", "profile_table", "retrieve_memory_item",
    "retrieve_turn", "list_earlier_turns",
})
MAX_BATCH = 6  # cap fan-out per decision so a bad batch can't blow the step budget
# Tools whose step should carry the exact SQL the system ran/handled, so the trace
# is a complete, clickable audit of every auto-executed statement.
_SQL_TOOLS = frozenset({"execute_sql", "execute_readonly_sql", "explain_sql",
                        "generate_sql", "validate_sql"})


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

RESULT_PREVIEW_LIMIT = 1400

# Circuit-breaker: if the same tool fails with the same error this many
# consecutive times, inject a strong hint so the model stops retrying.
_STUCK_LOOP_THRESHOLD = 3


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
    answer_language: str = "en"
    calls: list[ToolCallRecord] = field(default_factory=list)


class AskAgentLoop:
    """Codex-style tool loop: LLM chooses tools until it finishes with an answer."""

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
        """Namespace a step event under the current intent's trace node (when this
        run is one of several sub-intents), so step ids don't collide and the steps
        nest under their intent."""
        if self._trace_parent and event.get("step"):
            event["node_id"] = f"{self._trace_parent}:step:{event['step']}"
            event["parent_id"] = self._agent_loop_node_id or self._trace_parent
        elif event.get("step") and self._agent_loop_node_id:
            event["parent_id"] = self._agent_loop_node_id
        return event

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
        """Run the tool loop — the single execution path (no staged fallback). Recovers
        from a bad model decision by retrying within the loop (budget-bounded); if it
        genuinely can't finish it returns an honest failure response rather than
        degrading to a weaker pipeline.

        ``trace_parent`` nests this run's step nodes under a parent trace node (used
        when several sub-intents run in one turn, so each intent's steps group under
        it and step ids don't collide across intents)."""
        orch = self.orchestrator
        self._trace_parent = trace_parent
        self._agent_loop_node_id = f"{trace_parent}:loop" if trace_parent else "loop"
        transcript: list[str] = []
        approved_risk_sql = ""
        approved_risk_args: dict[str, Any] = {}
        approved_risk_tool = "execute_sql"

        if resume_state:
            transcript, execute = restore_loop_state(orch, resume_state)
            reply = str(user_reply or question or "").strip()
            if reply:
                transcript.append(f"User reply: {reply}")
                pending_question = (
                    orch.run_state.clarify_questions
                    or orch.run_state.pending_question
                    or "pending clarification"
                )
                # Every ask_user pause is a clarification chosen by the main brain.
                # Preserve the answered question so later decisions and SQL generation
                # know what the reply refers to.
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
                    orch.run_state.memory.resolve_open_question(pending_question)
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
                        transcript.append("Risk confirmation: user approved execution.")
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
                orch.run_state.memory.record_work(
                    action="user_reply",
                    args={"reply": reply},
                    ok=True,
                    summary="User replied to a pending confirmation/clarification.",
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
            if orch.run_state.answer and not orch.run_state.query_result:
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
            data = result.data if isinstance(result.data, dict) else {}
            if result.error:
                data = dict(data)
                data["error"] = result.error.to_dict()
            artifacts = [str(data[key]) for key in ("report_id", "artifact_id") if data.get(key)]
            orch.run_state.memory.record_work(
                action=approved_risk_tool,
                args=approved_risk_args or {"sql": approved_risk_sql},
                ok=result.ok,
                summary=brief_tool_summary(approved_risk_tool, result),
                purpose="execute the SQL the user just confirmed",
                artifacts=artifacts,
                data=data if isinstance(data, dict) else None,
            )
            done_event = self._ns_step(progress_event(
                stage=approved_risk_tool,
                title=f"{approved_risk_tool} done",
                status="completed" if result.ok else "failed",
                kind="tool",
                detail=brief_tool_summary(approved_risk_tool, result),
                step=1,
            ))
            if result.ok:
                done_event["sql"] = approved_risk_sql
                if data.get("row_count") is not None:
                    done_event["row_count"] = data.get("row_count")
            self.progress(done_event)
            if result.ok:
                summary = _summarize_tool_result(approved_risk_tool, result)
                transcript.append(f"Tool `{approved_risk_tool}` → {summary}")
            else:
                reason = result.error or brief_tool_summary(approved_risk_tool, result) or "confirmed_execution_failed"
                return self._build_failed_response(orch, reason, disclosures_before or [])

        # Step numbering must be CONTINUOUS across an ask_user pause/resume — node
        # IDs are derived from step_no (decision:N, step:N), and the trace is one
        # tree, so resuming from 0 would collide with the pre-pause nodes and
        # overwrite them in TraceModel. Pick up where the prior run paused.
        step_no = int(resume_state.get("step_base") or 0) if resume_state else 0
        if approved_risk_sql:
            step_no += 1
        recorder = getattr(orch, "llm_recorder", None)
        while runtime.steps_remaining > 0:
            decide_start = recorder.snapshot_len() if recorder else 0
            try:
                with llm_stage("decide"):
                    decision = self._decide(state, transcript)
            except LoopDecisionError as exc:
                logger.warning("loop_decision_failed: %s", exc)
                transcript.append(
                    "Error: could not parse a valid decision "
                    f"({exc}). Return ONLY one JSON object with action='call_tool' "
                    "(tool + args) or action='finish' (answer)."
                )
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
            tool_llm_start = recorder.snapshot_len() if recorder else 0
            action = str(decision.get("action") or "").strip().lower()
            decision_calls = recorder.since(decide_start) if recorder is not None else []
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
            if decision_calls:
                ev["llm_calls"] = decision_calls
            self.progress(ev)
            if action == "finish":
                answer = str(decision.get("answer") or "").strip()
                if not answer or answer == "Query complete.":
                    answer = self._answer_from_state(orch)
                if not answer:
                    # Don't degrade — push back and let the model actually answer (or
                    # call a tool to gather what it's missing). Budget-bounded retry.
                    transcript.append("Error: you called finish with an empty answer. "
                                      "Provide the markdown answer, or call a tool to get what you need.")
                    runtime.consume_step()
                    continue
                # The decide call that chose to finish runs outside any tool step;
                # surface it (prompt+response) as its own debug-trace step.
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
                    transcript.append(
                        "Note: these can't be batched (issue them one at a time so each keeps "
                        f"its gate / depends on prior results): {', '.join(dropped)}."
                    )
                if not calls:
                    if not dropped:
                        transcript.append(
                            "Error: action 'call_tools' needs a non-empty 'calls' list of "
                            'independent read-only tools ({"tool":...,"args":...}).'
                        )
                    runtime.consume_step()
                    continue
                paused = False
                for call in calls:
                    if runtime.steps_remaining <= 0:
                        break
                    step_no += 1
                    llm_start = recorder.snapshot_len() if recorder is not None else 0
                    sig = self._run_tool_call(orch, state, transcript, runtime, tool_ctx,
                                              decision, call["tool"], call["args"], step_no,
                                              llm_start, recorder)
                    if sig == "pending":
                        paused = True
                        break
                if paused:
                    return self._build_wait_response(
                        orch, state, transcript, disclosures_before or [], step_base=step_no,
                    )
                continue

            if action != "call_tool":
                logger.warning("loop_unknown_action: %s", action)
                # Retry rather than bail: tell the model the only valid actions.
                transcript.append(f"Error: unknown action {action!r}. Use 'call_tool' (one tool), "
                                  "'call_tools' (several independent read-only tools), or 'finish'.")
                runtime.consume_step()
                continue

            tool_name = str(decision.get("tool") or "").strip()
            if tool_name not in self.allowed_tool_names:
                allowed = ", ".join(sorted(self.allowed_tool_names))
                transcript.append(
                    f"Error: tool {tool_name!r} is not available in this loop. "
                    f"Use one of the advertised tools: {allowed}."
                )
                runtime.consume_step()  # charge budget so a repeated bad name can't loop forever
                continue

            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            step_no += 1
            llm_start = recorder.snapshot_len() if recorder is not None else 0
            sig = self._run_tool_call(orch, state, transcript, runtime, tool_ctx, decision,
                                      tool_name, args, step_no, llm_start, recorder)
            if sig == "pending":
                return self._build_wait_response(
                    orch, state, transcript, disclosures_before or [], step_base=step_no,
                )

        # Distinguish running out of the step budget mid-task from a clean finish —
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
        """Validate an action='call_tools' decision into a safe, ordered list of calls.

        Returns (runnable, dropped): only INDEPENDENT read-only tools that are both
        advertised and in BATCHABLE_TOOLS are runnable (capped at MAX_BATCH); anything
        else — SQL execution, ask_user, writes, unknown names — is dropped and named
        so the model re-issues it on its own turn with its gate intact."""
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
                if len(runnable) < MAX_BATCH:
                    args = item.get("args") if isinstance(item.get("args"), dict) else {}
                    runnable.append({"tool": tool, "args": args})
            elif tool not in dropped:
                dropped.append(tool)
        return runnable, dropped

    def _run_tool_call(self, orch: AskOrchestrator, state: LoopState, transcript: list[str],
                       runtime: AgentRuntime, tool_ctx: ToolContext, decision: dict[str, Any],
                       tool_name: str, args: dict[str, Any], step_no: int,
                       llm_start: int, recorder: Any) -> str:
        """Run one tool: emit trace, invoke, record into memory, and emit the done
        frame. Returns 'pending' (tool paused for the user) or 'ok'. Shared by single
        and batched dispatch so both paths record/trace identically."""
        self.progress(self._ns_step(progress_event(
            stage=tool_name, title=f"Calling {tool_name}", status="running", kind="tool",
            detail=str(args)[:200] if args else "", step=step_no,
        )))
        # Expose this step's trace node so the tool's sub-agents/sub-tools nest under it.
        orch.run_state.trace_node = (f"{self._trace_parent}:step:{step_no}"
                                     if self._trace_parent else f"step:{step_no}")
        with llm_stage(tool_name):
            result = runtime.call_tool(tool_name, args, tool_ctx)
        summary = _summarize_tool_result(tool_name, result)
        brief = brief_tool_summary(tool_name, result)
        state.calls.append(ToolCallRecord(tool=tool_name, args=args, ok=result.ok, summary=summary))
        transcript.append(f"Tool `{tool_name}` → {summary}")
        # ── Circuit-breaker: detect repeated identical failures ──
        # If the last N calls are the same tool with the same error, the model
        # is stuck retrying a validation bug.  Inject explicit guidance so it
        # stops wasting the step budget.
        if not result.ok:
            _inject_stuck_loop_hint(state, transcript)
        artifacts: list[str] = []
        data_for_memory = result.data if isinstance(result.data, dict) else {}
        if result.error:
            data_for_memory = dict(data_for_memory)
            data_for_memory["error"] = result.error.to_dict()
        if isinstance(data_for_memory, dict):
            for key in ("report_id", "artifact_id"):
                if data_for_memory.get(key):
                    artifacts.append(str(data_for_memory[key]))
            if data_for_memory.get("pending"):
                q = str(data_for_memory.get("question") or orch.run_state.pending_question or "")
                if q:
                    orch.run_state.memory.add_open_question(q)
        orch.run_state.memory.record_work(
            action=tool_name, args=args, ok=result.ok, summary=brief or summary,
            purpose=str(decision.get("thought") or "").strip(), artifacts=artifacts,
            data=data_for_memory if isinstance(data_for_memory, dict) else None,
        )
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
        if summary and summary != done_detail:
            done_event["output"] = summary
        if recorder is not None:
            iter_calls = recorder.since(llm_start)
            if iter_calls:
                done_event["llm_calls"] = iter_calls
        if isinstance(result.data, dict):
            done_event["result_data"] = result.data
        if executed_sql:
            done_event["sql"] = executed_sql
            data = result.data if isinstance(result.data, dict) else {}
            if data.get("row_count") is not None:
                done_event["row_count"] = data.get("row_count")
            db = str(data.get("database") or orch.run_state.database or "").strip()
            if db:
                done_event["database"] = db
        self.progress(done_event)
        if result.ok and isinstance(result.data, dict) and result.data.get("pending"):
            return "pending"
        return "ok"

    def _fail(self, reason: str) -> None:
        self.orchestrator.run_state.fail_reason = reason

    def _build_failed_response(
        self, orch: AskOrchestrator, reason: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        """Honest failure (no degrade): surface whatever real result/answer the loop did
        produce, otherwise a clear 'couldn't complete' message — with the reason kept in
        warnings for debugging. Marks the run failed in the trace."""
        self._fail(reason)
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
        return AssistantResponse(
            answer=answer,
            sql=orch.run_state.sql or "",
            result=orch.run_state.query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[t("agent.loop_failed_reason", reason=reason)],
        )

    def _emit_answer_chunk(self, text: str) -> None:
        """Forward a streamed slice of the final answer to the UI. Tagged so the UI
        routes it to the answer block, not the trace."""
        if text:
            self.progress({"kind": "answer_chunk", "text": text})

    def _apply_decision_memory(self, decision: dict[str, Any]) -> None:
        mem = self.orchestrator.run_state.memory
        # The model's read of the previous step's result, attached to that step so
        # the work log records did-what → result → judgment in one place.
        assessment = str(decision.get("result_assessment") or "").strip()
        if assessment:
            mem.note_last_judgment(assessment)
        updates = decision.get("memory_updates") if isinstance(decision.get("memory_updates"), dict) else {}
        for item in _list_update_items(updates.get("findings")):
            if isinstance(item, dict):
                mem.add_finding(
                    str(item.get("text") or ""),
                    source=str(item.get("source") or "model_note"),
                    confidence="model_note",
                )
            else:
                mem.add_finding(str(item), source="model_note", confidence="model_note")
        for item in _list_update_items(updates.get("verified")) + _list_update_items(updates.get("confirmed")):
            if isinstance(item, dict):
                mem.mark_verified(str(item.get("text") or ""), source=str(item.get("source") or "verified"))
            else:
                mem.mark_verified(str(item), source="verified")
        for item in _list_update_items(updates.get("open_questions")):
            mem.add_open_question(str(item.get("text") if isinstance(item, dict) else item))
        for item in _list_update_items(updates.get("hypotheses")):
            mem.add_hypothesis(str(item.get("text") if isinstance(item, dict) else item))
        for item in _list_update_items(updates.get("excluded_paths")) + _list_update_items(updates.get("exclusions")):
            if isinstance(item, dict):
                mem.add_exclusion(
                    str(item.get("target") or ""),
                    str(item.get("reason") or ""),
                    evidence_ref=str(item.get("evidence_ref") or "decision"),
                )
        hint = str(decision.get("next_action_hint") or "").strip()
        if hint:
            mem.next_action_hint = hint[:500]

    def _decide(self, state: LoopState, transcript: list[str]) -> dict[str, Any]:
        tools = self.allowed_tool_specs
        tool_lines = "\n".join(tool_prompt_line(s) for s in tools)
        execute_note = "allowed" if state.execute_allowed else "disabled"

        system = self.prompts.system_prompt(state, tool_lines, execute_note)
        user = self.prompts.user_prompt(state, transcript)

        last_error = ""
        for attempt in range(DECISION_RETRIES):
            messages = [LLMMessage("system", system), LLMMessage("user", user)]
            if last_error:
                messages.append(LLMMessage("user", f"Previous decision invalid: {last_error}. Try again."))
            schema_hint = ('Return {"action":"call_tool|call_tools|finish","tool":"...","args":{},'
                           '"calls":[{"tool":"...","args":{}}],"thought":"...","answer":"..."}')
            orch = self.orchestrator
            try:
                if orch.cancel_check:
                    orch.cancel_check()
                if getattr(orch, "stream_answers", False) and orch.llm.supports_streaming():
                    # Stream the decision; surface only the "answer" field's tokens live
                    # so the FINAL answer arrives token-by-token (intermediate call_tool
                    # decisions have no answer field → nothing streams). The full JSON is
                    # still parsed below, so the answer is authoritative regardless.
                    streamer = JsonFieldStreamer(self._emit_answer_chunk, field="answer")
                    def on_chunk(chunk: str) -> None:
                        if orch.cancel_check:
                            orch.cancel_check()
                        streamer.feed(chunk)
                    payload = orch.llm.complete_json_stream(
                        messages, schema_hint=schema_hint, on_text_chunk=on_chunk
                    )
                else:
                    payload = orch.llm.complete_json(messages, schema_hint=schema_hint)
                if orch.cancel_check:
                    orch.cancel_check()
            except ValueError as exc:
                # Malformed JSON (e.g. bad escaping) — don't abort the whole run; feed the
                # error back and let the model re-emit valid JSON on the next attempt.
                last_error = f"response was not valid JSON ({exc}); return ONLY a JSON object"
                logger.warning("loop_decide_parse_failed: %s", exc)
                continue
            except Exception as exc:
                if _looks_cancelled(exc):
                    raise
                last_error = f"LLM call failed ({type(exc).__name__}: {exc}); retry the decision"
                logger.warning("loop_decide_call_failed: %s", exc)
                continue
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
            # Tolerate the model naming a tool directly as the action, e.g.
            # {"action":"ask_user","question":"..."} instead of
            # {"action":"call_tool","tool":"ask_user","args":{...}}. Coerce it rather
            # than failing the whole run — this once turned a correct "ask the user"
            # into a hard failure after the model had done the right thing.
            if action in self.allowed_tool_names:
                args = payload.get("args") if isinstance(payload.get("args"), dict) else {
                    k: v for k, v in payload.items()
                    if k not in ("action", "tool", "args", "thought", "result_assessment",
                                 "memory_updates", "next_action_hint", "answer")
                }
                return {**payload, "action": "call_tool", "tool": action, "args": args}
            last_error = f"invalid action payload: {payload!r}"
        raise LoopDecisionError(last_error or "no valid decision")

    def _answer_from_state(self, orch: AskOrchestrator) -> str:
        if orch.run_state.query_result and orch.run_state.sql:
            # Drop the generic English placeholder — it added a stray line in the
            # wrong language; only a real rationale (already UI-language) is shown.
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
        transcript: list[str],
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
        snapshot = dump_loop_state(orch, transcript=transcript, execute_allowed=state.execute_allowed)
        # Carry the current step count so resume can keep decision:/step: node IDs
        # unique across the pause (TraceModel keys nodes by id; duplicate ids would
        # collapse the resumed steps onto the pre-pause ones).
        snapshot["step_base"] = int(step_base)
        # Nest the wait marker UNDER the ask_user tool step, not at the trace root —
        # the pause was caused by that tool call, so the hierarchy should read
        # loop → ask_user step → "Waiting for user clarification". Without an
        # explicit parent_id this event used to land outside the loop node.
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
        # Record the exact clarification so a copied trace shows what was asked and the
        # candidate options the user chose between (previously absent from copies).
        wait_event["question"] = question
        if options:
            wait_event["options"] = options
        structured = list(orch.run_state.pending_questions)
        if structured:
            wait_event["questions"] = structured
        self.progress(wait_event)
        return AssistantResponse(
            answer=answer,
            sql=orch.run_state.sql or "",
            result=None,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
            status="wait_user",
            pending_question=question,
            pending_options=options,
            pending_questions=list(orch.run_state.pending_questions),
            resume_state=snapshot,
        )

    def _build_response(
        self, orch: AskOrchestrator, answer: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        return AssistantResponse(
            answer=answer,
            sql=orch.run_state.sql or "",
            result=orch.run_state.query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
        )

    def _trace_sink(self, event: TraceEvent) -> None:
        # Tool loop emits structured progress for tool calls; registry/runtime traces duplicate it.
        if event.actor in {"tool", "runtime"}:
            return
        self.progress(from_trace_event(event))


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


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _list_update_items(value: Any) -> list[Any]:
    """Only list-valued memory_updates are accepted.

    Model-side shape mistakes should not poison compressed memory by iterating a
    string/dict as many tiny items. The next decision retry or tool evidence can
    still provide valid structured updates.
    """
    return value if isinstance(value, list) else []


def _inject_stuck_loop_hint(state: LoopState, transcript: list[str]) -> None:
    """If the last N calls are the same tool with the same error, inject a
    transcript hint telling the model it's stuck.

    This prevents the model from wasting the entire step budget retrying a
    validation bug (e.g. the SchemaGuard EXTRACT/FROM false-positive that
    caused 66 identical retries in production).
    """
    n = _STUCK_LOOP_THRESHOLD
    recent = state.calls[-n:]
    if len(recent) < n:
        return
    if all(not r.ok for r in recent) and len({r.tool for r in recent}) == 1 and len({r.summary for r in recent}) == 1:
        logger.warning("stuck_loop_detected tool=%s repeats=%d", recent[0].tool, n)
        transcript.append(
            f"WARNING: tool `{recent[0].tool}` has failed {n} consecutive times "
            f"with the same error. This is likely a validation limitation, not "
            f"a fixable SQL issue. DO NOT retry the same approach — either try "
            f"a fundamentally different SQL structure, use a different tool, "
            f"or call finish with an explanation of the limitation."
        )


def _looks_cancelled(exc: Exception) -> bool:
    return isinstance(exc, CancelledError)
