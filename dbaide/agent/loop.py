"""LLM tool-calling loop for Ask agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from dbaide.agent.answer_stream import JsonFieldStreamer
from dbaide.agent.loop_state import dump_loop_state, restore_loop_state
from dbaide.agent.progress_events import brief_tool_summary, from_trace_event, progress_event, progress_label
from dbaide.agent.schema_context import disclosed_table_keys
from dbaide.i18n import answer_language_directive
from dbaide.agent.runtime import AgentRuntime
from dbaide.agent.toolkit import build_tool_registry, loop_tool_specs
from dbaide.core.events import TraceEvent, TraceKind, TraceLevel
from dbaide.core.result import ExecutionPolicy
from dbaide.llm import LLMMessage
from dbaide.models import AssistantResponse
from dbaide.tools.registry import ToolContext, ToolResult

if True:  # TYPE_CHECKING without circular import at runtime
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.agent.loop")

DECISION_RETRIES = 3
# Both names bind to the same execute handler; the loop must treat them alike.
_EXECUTE_TOOLS = frozenset({"execute_sql", "execute_readonly_sql"})
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
    return str(getattr(orch, "_loop_sql", "") or "").strip()
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
        self._trace_parent = ""

    def _ns_step(self, event: dict) -> dict:
        """Namespace a step event under the current intent's trace node (when this
        run is one of several sub-intents), so step ids don't collide and the steps
        nest under their intent."""
        if self._trace_parent and event.get("step"):
            event["node_id"] = f"{self._trace_parent}:step:{event['step']}"
            event["parent_id"] = self._trace_parent
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
        transcript: list[str] = []

        if resume_state:
            transcript, execute = restore_loop_state(orch, resume_state)
            reply = str(user_reply or question or "").strip()
            if reply:
                transcript.append(f"User reply: {reply}")
                # If the pause was a business-criteria (口径) clarification, fold the
                # reply into the confirmed criteria so generate_sql honours it exactly.
                if getattr(orch, "_loop_clarify_questions", ""):
                    orch._loop_clarifications.append(
                        f"User confirmed the following criteria — {orch._loop_clarify_questions}\n"
                        f"User's answer: {reply}"
                    )
                    orch._loop_clarify_questions = ""
                self.progress(
                    progress_event(stage="user", title=f"Reply: {reply[:120]}", status="completed", kind="user"),
                )
            state = LoopState(
                question=str(resume_state.get("question") or question),
                database=str(resume_state.get("database") or database),
                execute_allowed=execute,
            )
            self.progress(
                progress_event(stage="loop", title="Resuming agent loop", status="running", kind="agent"),
            )
        else:
            orch._reset_loop_state(question, database, execute)
            orch.schema.disclose_instance()
            state = LoopState(question=question, database=database, execute_allowed=execute)
            self.progress(
                progress_event(stage="loop", title="Agent loop started", status="running", kind="agent"),
            )
        tool_ctx = ToolContext(
            execution_policy=orch.execution_policy.value,
            trace_sink=self._trace_sink,
        )
        runtime = AgentRuntime(
            llm=orch.llm,
            tool_registry=self.registry,
            execution_policy=orch.execution_policy,
            trace_sink=self._trace_sink,
            max_steps=orch.session.agent_max_steps,
        )

        step_no = 0
        while runtime.steps_remaining > 0:
            try:
                decision = self._decide(state, transcript)
            except LoopDecisionError as exc:
                logger.warning("loop_decision_failed: %s", exc)
                return self._build_failed_response(orch, f"decision_invalid: {exc}", disclosures_before or [])

            action = str(decision.get("action") or "").strip().lower()
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
                self.progress(
                    progress_event(stage="loop", title="Agent loop finished", status="completed", kind="agent"),
                )
                return self._build_response(orch, answer, disclosures_before or [])

            if action != "call_tool":
                logger.warning("loop_unknown_action: %s", action)
                # Retry rather than bail: tell the model the only valid actions.
                transcript.append(f"Error: unknown action {action!r}. "
                                  "Use action 'call_tool' (with a tool) or 'finish' (with an answer).")
                runtime.consume_step()
                continue

            tool_name = str(decision.get("tool") or "").strip()
            if tool_name not in self.registry._handlers:  # noqa: SLF001
                transcript.append(f"Error: unknown tool {tool_name!r}. Use a registered tool name.")
                runtime.consume_step()  # charge budget so a repeated bad name can't loop forever
                continue

            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            thought = str(decision.get("thought") or "").strip()
            if thought:
                self.progress(
                    progress_event(stage="decision", title=thought[:200], status="completed", kind="decision"),
                )

            step_no += 1
            self.progress(
                self._ns_step(progress_event(
                    stage=tool_name,
                    title=f"Calling {tool_name}",
                    status="running",
                    kind="tool",
                    detail=str(args)[:200] if args else "",
                    step=step_no,
                ))
            )
            # Expose this step's trace node id so the tool's sub-agents/sub-tools nest
            # under it (true call hierarchy), not flattened by stage-name resolution.
            orch._loop_trace_node = (f"{self._trace_parent}:step:{step_no}"
                                     if self._trace_parent else f"step:{step_no}")
            result = runtime.call_tool(tool_name, args, tool_ctx)
            summary = _summarize_tool_result(tool_name, result)
            brief = brief_tool_summary(tool_name, result)
            state.calls.append(ToolCallRecord(tool=tool_name, args=args, ok=result.ok, summary=summary))
            transcript.append(f"Tool `{tool_name}` → {summary}")
            # Put the exact SQL on the execute/explain step itself, so clicking the
            # step in the trace surfaces the SQL the system ran (full auditability).
            done_detail = brief
            executed_sql = _executed_sql(tool_name, orch, result)
            if executed_sql:
                done_detail = executed_sql
            done_event = progress_event(
                stage=tool_name,
                title=f"{tool_name} done",
                status="completed" if result.ok else "failed",
                kind="tool",
                detail=done_detail,
                duration_ms=float(getattr(result, "duration_ms", 0) or 0),
                step=step_no,
            )
            done_event = self._ns_step(done_event)
            # Carry the tool's INPUT (full args, not the truncated 'Calling' preview)
            # and OUTPUT (full result summary) so a copied trace fully describes the
            # step — the running 'Calling' frame is overwritten by this 'done' frame,
            # so anything not put here is lost from the persisted trace.
            if args:
                done_event["args"] = args
            if summary and summary != done_detail:
                done_event["output"] = summary
            if executed_sql:
                done_event["sql"] = executed_sql
                # Carry the SQL facts so the typed SQL step can show rows/db on click.
                data = result.data if isinstance(result.data, dict) else {}
                if data.get("row_count") is not None:
                    done_event["row_count"] = data.get("row_count")
                db = str(data.get("database") or orch._loop_database or "").strip()
                if db:
                    done_event["database"] = db
            self.progress(done_event)

            # Any tool may pause for the user (ask_user, or resolve_schema when the
            # question is ambiguous) by returning a pending question.
            if result.ok and isinstance(result.data, dict) and result.data.get("pending"):
                return self._build_wait_response(orch, state, transcript, disclosures_before or [])

            if tool_name == "describe_table" and result.ok:
                auto = self._auto_get_relations_if_needed(state, runtime, tool_ctx)
                if auto is not None:
                    summary = _summarize_tool_result("get_relations", auto)
                    brief = brief_tool_summary("get_relations", auto)
                    state.calls.append(
                        ToolCallRecord(tool="get_relations", args={}, ok=auto.ok, summary=summary),
                    )
                    transcript.append(f"Tool `get_relations` (auto) → {summary}")

            if tool_name in _EXECUTE_TOOLS and result.ok:
                break
            if tool_name in _EXECUTE_TOOLS and isinstance(result.data, dict) and result.data.get("blocked"):
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
        return self._build_failed_response(orch, "step_budget_exhausted", disclosures_before or [])

    def _fail(self, reason: str) -> None:
        self.orchestrator._loop_fail_reason = reason

    def _build_failed_response(
        self, orch: AskOrchestrator, reason: str, disclosures_before: list[str],
    ) -> AssistantResponse:
        """Honest failure (no degrade): surface whatever real result/answer the loop did
        produce, otherwise a clear 'couldn't complete' message — with the reason kept in
        warnings for debugging. Marks the run failed in the trace."""
        self._fail(reason)
        self.progress(
            progress_event(stage="loop", title="Agent loop stopped", status="failed",
                           kind="agent", detail=reason),
        )
        from dbaide.i18n import t
        answer = (orch._loop_answer or "").strip()
        if not answer and orch._loop_query_result:
            answer = self._answer_from_state(orch)
        if not answer:
            answer = t("agent.loop_failed")
        return AssistantResponse(
            answer=answer,
            sql=orch._loop_sql or "",
            result=orch._loop_query_result,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[t("agent.loop_failed_reason", reason=reason)],
        )

    def _auto_get_relations_if_needed(
        self,
        state: LoopState,
        runtime: AgentRuntime,
        tool_ctx: ToolContext,
    ) -> ToolResult | None:
        """Codex-style deterministic step: load FKs once multiple tables are disclosed."""
        orch = self.orchestrator
        tables = disclosed_table_keys(orch)
        if len(tables) < 2:
            return None
        for call in state.calls:
            if call.tool == "get_relations" and call.ok:
                return None
        self.progress(
            progress_event(
                stage="get_relations",
                title="Auto: loading foreign-key relations",
                status="running",
                kind="decision",
                detail=", ".join(f"{db}.{t}" for db, t in tables),
            ),
        )
        # Cheap auto-load: declared FKs + catalog only. Semantic inference is a
        # last-resort, on-demand step (generate_sql runs it for the tables it joins).
        result = runtime.call_tool("get_relations", {"infer_semantic": False}, tool_ctx)
        brief = brief_tool_summary("get_relations", result)
        self.progress(
            progress_event(
                stage="get_relations",
                title="get_relations done (auto)",
                status="completed" if result.ok else "failed",
                kind="tool",
                detail=brief,
                duration_ms=float(getattr(result, "duration_ms", 0) or 0),
            ),
        )
        return result

    def _emit_answer_chunk(self, text: str) -> None:
        """Forward a streamed slice of the final answer to the UI. Tagged so the UI
        routes it to the answer block, not the trace."""
        if text:
            self.progress({"kind": "answer_chunk", "text": text})

    def _decide(self, state: LoopState, transcript: list[str]) -> dict[str, Any]:
        tools = loop_tool_specs(self.registry)
        tool_lines = "\n".join(f"- {s.name}: {s.description}" for s in tools)
        policy = self.orchestrator.execution_policy.value
        execute_note = "allowed" if state.execute_allowed else "disabled"

        system = (
            "You are DBAide, a database assistant operating in a tool loop.\n"
            "Choose the next action to answer the user.\n\n"
            "How to work (read carefully):\n"
            "• Big direction first, then detail — get the relevant tables, THEN their columns. "
            "Never scan the whole database to find a needle.\n"
            "• Assets first — discover_schema and resolve_schema read the offline assets and narrow "
            "by relevance (instance → database → table). They are the default way to learn schema.\n"
            "• Only touch the live database (list_tables / describe_table / SQL) when the assets can't "
            "answer it — e.g. assets are missing, or a tool reports a missing table/column. Do NOT open "
            "with list_tables; it returns everything and wastes steps.\n"
            "• Be decisive: take the fewest steps. Confirm what you can in one shot; only iterate when the "
            "task is genuinely progressive (need B's result to do C).\n"
            "• NEVER GUESS the meaning of the question. A query runs against real data — a wrong "
            "interpretation gives a confidently wrong number. If ANYTHING about how to answer is uncertain "
            "and would change the result — which table/column to use, what a status/flag/category value "
            "means, how time is stored or which timezone a window means, what a metric counts, which rows "
            "are in scope, units — you MUST confirm with the user (clarify_semantics, or ask_user) rather "
            "than assume. This is open-ended, not a checklist; it can arise at ANY step (while exploring, "
            "after seeing a column's values, before writing SQL) and more than once. Never invent a "
            "default that presumes a business fact (a timezone, a status value, a region, a table).\n\n"
            f"Execution policy: {policy} (execute_sql is {execute_note})\n\n"
            "Available tools:\n"
            f"{tool_lines}\n\n"
            "Return JSON only:\n"
            '  {"action":"call_tool","tool":"resolve_schema","args":{"question":"..."},"thought":"..."}\n'
            '  {"action":"finish","answer":"markdown answer for the user"}\n\n'
            "Guidelines:\n"
            "- Schema / where-is questions: discover_schema → synthesize_schema_answer → finish\n"
            "- Data queries: resolve_schema → clarify_semantics → generate_sql → validate_sql"
            + (" → execute_sql → finish" if state.execute_allowed and policy not in ("sql_only", "inspect_only") else " → finish")
            + "\n"
            "- resolve_schema returns the MINIMAL tables/columns + joins in ONE step; generate_sql then uses exactly that. Prefer it over manual discover/describe for data queries — don't re-explore what it already resolved.\n"
            "- clarify_semantics: ALWAYS resolve_schema FIRST so clarification is grounded in the real tables/columns/values — never clarify before the relevant schema is in hand (an ungrounded question invites guessing). It inspects the resolved schema (and the real observed values of its columns) and surfaces EVERY genuinely-uncertain interpretation as a question for the user — open-ended, whatever applies (value encodings, which column, time/timezone, metric definition, scope, …), with its candidate options drawn from the actual schema/values. If it pauses, wait for the reply; if it returns clear, proceed. You may also call it again later if new uncertainty appears (e.g. after inspecting more data). Do not substitute your own guess for it.\n"
            "- Only fall back to manual describe_table/get_relations if resolve_schema is insufficient or validate_sql reports a missing table/column.\n"
            "- get_relations already includes sample evidence; do not call validate_joins unless user explicitly asks to re-check joins.\n"
            "- Saved joins (user catalog) are loaded automatically inside get_relations — no join CRUD during queries.\n"
            "- If schema is ambiguous or multiple valid interpretations exist, call ask_user before guessing — and GROUND it in the schema: when the uncertainty is which column/table/value, pass the actual candidate column names, table names, or observed values (from the resolved schema you already have) as `options` so the user just picks one. Never ask an open 'which field?' question when the candidates are already in the schema; leave options empty only when the answer is genuinely outside the schema (e.g. a timezone).\n"
            "- ask_user pauses the run until the user replies; the next user message resumes the same workflow.\n"
            "- If validate_sql reports unknown tables/columns, describe_table then retry generate_sql.\n"
            "- describe_table returns the table's full structure (columns, types, indexes, FKs) plus a small sample — the table is the lowest pre-built level; there are no per-column docs.\n"
            "- For a column's value ranges / null rate / distinct / length, call column_stats (pick only the metrics you need); for a whole-table overview omit columns. To learn a column's actual values (e.g. which status/flag value means what), use column_stats with metrics=[\"top_values\"].\n"
            "- Do NOT repeat the same tool call with the same args. If a tool didn't give you what you need (empty/unchanged result, or a `note` that something isn't available), change approach — and if you still can't determine a value's business meaning, ask_user (or clarify_semantics) ONCE rather than profiling again.\n"
            "- Profile questions: discover_schema → describe_table → column_stats → finish\n"
            "- SQL explain: validate_sql or explain_sql as needed → finish\n"
            "- Do not invent tables or columns. Prefer precision over listing everything.\n"
            "- When you have enough to answer, use action=finish.\n"
            f"- {answer_language_directive()}"
        )

        history = "\n\n".join(transcript[-8:]) if transcript else "(no tool calls yet)"
        # Surface the user's pinned schema (composer attachments) so the model knows
        # which tables were explicitly attached and can resolve_schema on them directly
        # instead of re-discovering the whole database.
        pins = _pinned_scope_labels(getattr(self.orchestrator, "schema_scope", None))
        pin_line = (f"User-attached schema (prefer these; resolve_schema on them directly, "
                    f"no broad discovery needed): {', '.join(pins)}\n\n") if pins else ""
        user = (
            f"User question:\n{state.question}\n\n"
            f"Database scope: {state.database or '(any)'}\n\n"
            f"{pin_line}"
            f"Tool history:\n{history}"
        )

        last_error = ""
        for attempt in range(DECISION_RETRIES):
            messages = [LLMMessage("system", system), LLMMessage("user", user)]
            if last_error:
                messages.append(LLMMessage("user", f"Previous decision invalid: {last_error}. Try again."))
            schema_hint = ('Return {"action":"call_tool|finish","tool":"...","args":{},'
                           '"thought":"...","answer":"..."}')
            orch = self.orchestrator
            try:
                if getattr(orch, "stream_answers", False) and orch.llm.supports_streaming():
                    # Stream the decision; surface only the "answer" field's tokens live
                    # so the FINAL answer arrives token-by-token (intermediate call_tool
                    # decisions have no answer field → nothing streams). The full JSON is
                    # still parsed below, so the answer is authoritative regardless.
                    streamer = JsonFieldStreamer(self._emit_answer_chunk, field="answer")
                    payload = orch.llm.complete_json_stream(
                        messages, schema_hint=schema_hint, on_text_chunk=streamer.feed
                    )
                else:
                    payload = orch.llm.complete_json(messages, schema_hint=schema_hint)
            except ValueError as exc:
                # Malformed JSON (e.g. bad escaping) — don't abort the whole run; feed the
                # error back and let the model re-emit valid JSON on the next attempt.
                last_error = f"response was not valid JSON ({exc}); return ONLY a JSON object"
                logger.warning("loop_decide_parse_failed: %s", exc)
                continue
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
            # Drop the generic English placeholder — it added a stray line in the
            # wrong language; only a real rationale (already UI-language) is shown.
            draft_rationale = orch._loop_sql_rationale or ""
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

    def _build_wait_response(
        self,
        orch: AskOrchestrator,
        state: LoopState,
        transcript: list[str],
        disclosures_before: list[str],
    ) -> AssistantResponse:
        question = orch._loop_pending_question
        options = list(orch._loop_pending_options)
        lines = [question]
        if options:
            lines.append("")
            lines.append("Options:")
            lines.extend(f"- {item}" for item in options)
        answer = "\n".join(lines)
        snapshot = dump_loop_state(orch, transcript=transcript, execute_allowed=state.execute_allowed)
        wait_event = progress_event(
            stage="ask_user",
            title="Waiting for user clarification",
            status="waiting",
            kind="user",
            detail=question,
        )
        # Record the exact clarification so a copied trace shows what was asked and the
        # candidate options the user chose between (previously absent from copies).
        wait_event["question"] = question
        if options:
            wait_event["options"] = options
        structured = list(orch._loop_pending_questions)
        if structured:
            wait_event["questions"] = structured
        self.progress(wait_event)
        return AssistantResponse(
            answer=answer,
            sql=orch._loop_sql or "",
            result=None,
            disclosures=orch.session.disclosure.events[len(disclosures_before):],
            warnings=[],
            status="wait_user",
            pending_question=question,
            pending_options=options,
            pending_questions=list(orch._loop_pending_questions),
            resume_state=snapshot,
        )

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
        # Tool loop emits structured progress for tool calls; registry/runtime traces duplicate it.
        if event.actor in {"tool", "runtime"}:
            return
        self.progress(from_trace_event(event))


def _pinned_scope_labels(scope: dict | None) -> list[str]:
    """Human-readable labels for the user's pinned schema scope (composer attachments):
    'db.table' for tables, 'db.*' for whole databases. Empty when nothing is pinned."""
    if not isinstance(scope, dict) or not scope:
        return []
    labels: list[str] = []
    for t in scope.get("tables") or []:
        db = str((t or {}).get("database") or "").strip()
        tbl = str((t or {}).get("table") or "").strip()
        if tbl:
            labels.append(f"{db}.{tbl}" if db else tbl)
    for db in scope.get("databases") or []:
        db = str(db or "").strip()
        if db:
            labels.append(f"{db}.*")
    return labels


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
