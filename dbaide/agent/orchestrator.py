"""Unified Ask orchestrator — LLM routing, progressive schema, SQL, risk control."""

from __future__ import annotations

import logging
from typing import Any, Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.agent.answerer import AnswerFormatter
from dbaide.agent.controllers import ResultInterpreter, RiskController
from dbaide.agent.progress_events import progress_event
from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
from dbaide.agent.run_state import RunState
from dbaide.agent.sql_writer import SQLWriter
from dbaide.charts.embed import merge_chart_specs, remap_chart_refs
from dbaide.joins import JoinCatalogStore
from dbaide.annotations import AnnotationStore
from dbaide.assets import AssetStore
from dbaide.connection_identity import connection_fingerprint
from dbaide.i18n import detect_user_language, normalize, t as _i18n_t
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import AssistantResponse
from dbaide.session import Session
from dbaide.tools import DiagnoseTools, ProfileTools, QueryTools, SchemaTools

logger = logging.getLogger("dbaide.orchestrator")


class AskOrchestrator:
    """Codex-style Ask agent: route → discover → act → validate → execute (with risk gate)."""

    def __init__(
        self,
        adapter: DatabaseAdapter,
        session: Session,
        llm: LLMClient | None = None,
        *,
        asset_store: AssetStore | None = None,
        join_catalog: JoinCatalogStore | None = None,
        annotations: AnnotationStore | None = None,
        progress: Callable[[Any], None] | None = None,
        model_config: Any = None,
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.instance = session.connection.name
        self.connection_fingerprint = connection_fingerprint(session.connection)
        llm = llm or NullLLMClient()
        # Debug trace: wrap a real client so every model call (full prompt+response)
        # is captured and attached to the trace. Gated by env so normal runs are lean.
        self.llm_recorder = None
        if not isinstance(llm, NullLLMClient):
            from dbaide.agent.llm_trace import RecordingLLMClient, tracing_enabled
            if tracing_enabled():
                llm = RecordingLLMClient(llm)
                self.llm_recorder = llm
        self.llm = llm
        self.model_config = model_config
        self.asset_store = asset_store or AssetStore()
        self.join_catalog = join_catalog or JoinCatalogStore()
        self.annotations = annotations or AnnotationStore()
        self.progress = progress or (lambda _msg: None)
        # User-pinned schema scope (set by the workflow from composer attachments).
        self.schema_scope: dict[str, Any] = {}
        # Stream the final answer token-by-token (set by the workflow from config).
        self.stream_answers: bool = False
        self.cancel_check: Callable[[], None] | None = None
        # Session memory (set by the workflow from ChatSessionStore each run):
        # every completed turn earlier in this chat session, plus the consolidated
        # set of user-confirmed criteria across the session. Used to render
        # [Prior turns] + [Active session criteria] in the user prompt and to back
        # the retrieve_turn / list_earlier_turns tools.
        self.session_turns: list[dict[str, Any]] = []
        self.active_criteria: list[str] = []
        # Session-level message continuity: when set, the loop appends to this
        # stream instead of creating fresh [system, user] messages each turn.
        self.session_messages: list[Any] | None = None
        self.subagent_depth: int = 0
        self.max_subagent_depth: int = 1

        self.schema = SchemaTools(adapter, session.disclosure, instance=self.instance, assets=self.asset_store)
        self.profile = ProfileTools(adapter, session.disclosure, instance=self.instance, assets=self.asset_store)
        self.query = QueryTools(
            adapter,
            session.disclosure,
            instance=self.instance,
            default_limit=session.default_limit,
            timeout_seconds=session.timeout_seconds,
        )
        self.diagnose = DiagnoseTools(self.query)

        try:
            server_version = adapter.server_version()
        except Exception:
            server_version = ""
        self.sql_writer = SQLWriter(
            llm,
            dialect=adapter.dialect,
            server_version=server_version,
            session_timezone=getattr(session.connection, "session_timezone", "UTC"),
        )
        self.formatter = AnswerFormatter()
        self.risk = RiskController()
        self.interpreter = ResultInterpreter()
        self._reset_loop_state("", "", False)

    def _reset_loop_state(self, question: str, database: str, execute: bool,
                          *, answer_language: str | None = None) -> None:
        """Start a fresh per-run state for one question (see RunState)."""
        self.run_state = RunState(
            question=question,
            database=database,
            execute_allowed=execute,
            table_database=database,
            answer_language=normalize(answer_language or detect_user_language(question)),
        )
        self.run_state.memory.reset_goal(question, database=database, execute_allowed=execute)
        # L2 carry-over: criteria the user confirmed earlier in THIS chat session
        # become the new turn's binding clarifications. The SQL writer applies them
        # verbatim via its [Business criteria] block, and the decision prompt sees
        # them in [Confirmed criteria] — so a follow-up doesn't lose 口径 ("Beijing
        # time", "paid only") that the user already settled.
        if self.active_criteria:
            self.run_state.clarifications = list(self.active_criteria)
        # Carry verified facts + ruled-out paths from earlier turns into this run's
        # memory so they survive even after the originating turn is compressed —
        # they are re-injected into the turn prompt (same rationale as criteria).
        self._seed_session_memory()

    def run(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
    ) -> AssistantResponse:
        if isinstance(self.llm, NullLLMClient):
            return AssistantResponse(
                answer=(
                    "**Model required**\n\n"
                    "Configure an LLM in Settings → Models (provider, base URL, API key, model ID), "
                    "then select it in the composer."
                ),
                warnings=["No LLM configured"],
            )

        # Resuming a paused run continues that single in-flight intent — never re-decompose.
        if resume_state or user_reply:
            multi = resume_state.get("multi") if isinstance(resume_state, dict) else None
            trace_parent = ""
            if isinstance(multi, dict) and isinstance(multi.get("paused"), dict):
                paused_id = str(multi["paused"].get("id") or "").strip()
                if paused_id:
                    trace_parent = f"intent:{paused_id}"
            resp = self._run_single(question, database=database, execute=execute,
                                    resume_state=resume_state, user_reply=user_reply,
                                    trace_parent=trace_parent,
                                    skip_turn_markers=bool(multi))
            # If the pause happened inside a multi-intent plan, resume the WHOLE plan
            # (the paused intent + any not-yet-run ones) rather than dropping the rest.
            if multi is not None:
                resume_database = (
                    str(resume_state.get("database") or database)
                    if isinstance(resume_state, dict)
                    else database
                )
                return self._continue_multi(multi, resp, database=resume_database, execute=execute)
            return resp

        from dbaide.agent.intent import IntentDecomposer
        from dbaide.agent.llm_trace import llm_stage
        rec = self.llm_recorder
        intent_start = rec.snapshot_len() if rec else 0
        try:
            with llm_stage("intent"):
                intents = IntentDecomposer(self.llm).decompose(question)
        except Exception as exc:
            logger.warning("intent_decompose_failed: %s", exc)
            intents = []
        # Intent decomposition runs before the tool loop, so surface it as a real
        # trace action even when full LLM prompt capture is disabled. When prompt
        # capture is enabled, attach the exact LLM input/output as well.
        intent_calls = rec.since(intent_start) if rec is not None else []
        ev = progress_event(
            stage="intent",
            title="Decompose intent",
            status="completed",
            kind="llm",
            node_id="intent:decompose",
            detail=question,
        )
        ev["result_data"] = {
            "intents": [
                {"id": it.id, "type": it.type, "text": it.text, "language": it.language, "label": it.label}
                for it in intents
            ],
        }
        if intent_calls:
            ev["llm_calls"] = intent_calls
        self.progress(ev)
        if len(intents) > 1:
            return self._run_multi(question, intents, database=database, execute=execute)
        answer_language = intents[0].language if intents else detect_user_language(question)
        return self._run_single(question, database=database, execute=execute,
                                answer_language=answer_language)

    def _run_single(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
        trace_parent: str = "",
        answer_language: str | None = None,
        skip_turn_markers: bool = False,
    ) -> AssistantResponse:
        self.run_state.fail_reason = ""  # fresh per run (never carry a stale reason)
        # Carry forward schema disclosed in EARLIER turns of this chat session so
        # the schema guard recognizes those tables this turn — the conversation
        # memory (session_messages / [Prior turns]) implies the agent already
        # knows them, and the disclosure gate must agree (else a follow-up that
        # reuses a prior table is wrongly rejected as "undisclosed"). Done before
        # snapshotting disclosures_before so these don't count as "new this turn".
        self._seed_session_disclosure()
        disclosures = list(self.session.disclosure.events)

        from dbaide.agent.loop import AskAgentLoop

        # The tool loop is the single execution path. It recovers from bad model
        # decisions by retrying within itself and, if it truly can't finish, returns an
        # honest failure response — we deliberately do NOT degrade to a weaker staged
        # pipeline. An unexpected exception is surfaced as a clean failure, not a silent
        # downgrade.
        try:
            return AskAgentLoop(self, progress=self.progress).run(
                question,
                database=database,
                execute=execute,
                disclosures_before=disclosures,
                resume_state=resume_state,
                user_reply=user_reply,
                trace_parent=trace_parent,
                answer_language=answer_language,
                session_messages=self.session_messages if not skip_turn_markers else None,
            )
        except Exception as exc:
            logger.warning("agent_loop_failed: %s", exc, exc_info=True)
            self.run_state.fail_reason = f"exception: {exc}"
            from dbaide.agent.sql_executions import response_sql_exports
            selected_sql, executed_sqls = response_sql_exports(self.run_state)
            return AssistantResponse(
                answer=_i18n_t("agent.loop_failed"),
                sql=selected_sql,
                result=self.run_state.query_result,
                disclosures=self._new_disclosures(disclosures),
                warnings=[_i18n_t("agent.loop_failed_reason", reason=str(exc))],
                charts=list(self.run_state.charts or []) or None,
                executed_sqls=executed_sqls or None,
            )

    def _run_multi(self, question: str, intents, *, database: str, execute: bool) -> AssistantResponse:
        """Run independent sub-intents in turn and aggregate. Each sub-intent keeps a
        self-contained answer + result, and its steps nest under an intent node in
        the trace so the user sees every sub-intent's execution."""
        from dbaide.agent.progress_events import progress_event

        disclosures_before = list(self.session.disclosure.events)
        self.progress(progress_event(
            stage="decompose", title=f"Decomposed into {len(intents)} sub-intents",
            status="completed", kind="phase", node_id="intent:plan",
        ))
        # Session continuity: multi-intent is one turn. Sub-intents run in
        # isolation; the aggregated answer is appended to the session stream.
        session_mode = self.session_messages is not None
        if session_mode:
            self._multi_begin_turn(question)
        results: list[tuple[Any, AssistantResponse]] = []
        for idx, intent in enumerate(intents, start=1):
            node_id = f"intent:{intent.id}"
            self.progress(progress_event(
                stage="intent", title=f"{idx}. {intent.label}: {intent.text}",
                status="running", kind="phase", node_id=node_id,
            ))
            resp = self._run_single(intent.text, database=database, execute=execute,
                                    trace_parent=node_id, answer_language=intent.language,
                                    skip_turn_markers=session_mode)
            self.progress(progress_event(
                stage="intent", title=f"{idx}. {intent.label}: {intent.text}",
                status="failed" if (resp.warnings and not resp.answer) else "completed",
                kind="phase", node_id=node_id,
            ))
            # If a sub-intent pauses for the user, surface it — but carry the plan
            # (completed answers + not-yet-run intents) so resume continues the whole
            # set instead of silently dropping the rest.
            if getattr(resp, "status", "completed") == "wait_user":
                resp.resume_state = self._attach_multi(
                    resp.resume_state, question, results, intent, intents[idx:],
                    disclosures_before=disclosures_before,
                )
                return resp
            results.append((intent, resp))
        aggregated = self._aggregate(question, results, disclosures_before=disclosures_before)
        if session_mode:
            self._multi_end_turn(aggregated)
        return aggregated

    @staticmethod
    def _ser_intent(intent) -> dict[str, Any]:
        return {"id": intent.id, "type": intent.type, "text": intent.text, "language": intent.language}

    def _attach_multi(self, resume_state, question, done_results, paused_intent, remaining,
                      *, disclosures_before: list[str] | None = None) -> dict[str, Any]:
        """Fold the multi-intent plan into the paused intent's resume snapshot."""
        state = dict(resume_state or {})
        state["multi"] = {
            "question": question,
            "disclosures_before": list(disclosures_before or []),
            "done": [
                {
                    "intent": self._ser_intent(it),
                    "answer": rp.answer,
                    "sql": rp.sql,
                    "disclosures": list(rp.disclosures or []),
                    "charts": list(rp.charts or []),
                    "executed_sqls": list(rp.executed_sqls or []),
                }
                for it, rp in done_results
            ],
            "paused": self._ser_intent(paused_intent),
            "remaining": [self._ser_intent(it) for it in remaining],
        }
        return state

    def _continue_multi(self, multi: dict[str, Any], paused_resp: AssistantResponse,
                        *, database: str, execute: bool) -> AssistantResponse:
        """After a paused sub-intent resumes, finish it + run the remaining sub-intents."""
        question = str(multi.get("question") or "")
        if self.session_messages is not None:
            from dbaide.agent.loop import AskAgentLoop
            self._multi_turn_number = AskAgentLoop._count_completed_turns(self.session_messages) + 1
        done: list[tuple[Any, AssistantResponse]] = [
            (
                _sub_intent_from_dict(d.get("intent") if isinstance(d, dict) else {}),
                AssistantResponse(
                    answer=d.get("answer") or "",
                    sql=d.get("sql") or "",
                    disclosures=list(d.get("disclosures") or []),
                    charts=list(d.get("charts") or []) or None,
                    executed_sqls=list(d.get("executed_sqls") or []) or None,
                ),
            )
            for d in (multi.get("done") or [])
            if isinstance(d, dict)
        ]
        paused_intent = _sub_intent_from_dict(multi.get("paused"))

        # The resumed intent paused AGAIN → re-attach the plan and keep waiting.
        if getattr(paused_resp, "status", "completed") == "wait_user":
            paused_resp.resume_state = self._attach_multi(
                paused_resp.resume_state, question, done, paused_intent,
                [_sub_intent_from_dict(r) for r in (multi.get("remaining") or []) if isinstance(r, dict)],
                disclosures_before=list(multi.get("disclosures_before") or []),
            )
            return paused_resp

        results = done + [(paused_intent, paused_resp)]
        remaining = [_sub_intent_from_dict(r) for r in (multi.get("remaining") or []) if isinstance(r, dict)]
        session_mode = self.session_messages is not None
        for i, intent in enumerate(remaining):
            resp = self._run_single(intent.text, database=database, execute=execute,
                                    trace_parent=f"intent:{intent.id}",
                                    answer_language=intent.language,
                                    skip_turn_markers=session_mode)
            if getattr(resp, "status", "completed") == "wait_user":
                resp.resume_state = self._attach_multi(
                    resp.resume_state, question, results, intent, remaining[i + 1:],
                    disclosures_before=list(multi.get("disclosures_before") or []),
                )
                return resp
            results.append((intent, resp))
        aggregated = self._aggregate(
            question,
            results,
            disclosures_before=list(multi.get("disclosures_before") or []),
        )
        if session_mode:
            self._multi_end_turn(aggregated)
        return aggregated

    def _multi_begin_turn(self, question: str) -> None:
        """Append a turn-start marker for the multi-intent as one logical turn."""
        from dbaide.agent.loop import AskAgentLoop
        from dbaide.llm import LLMMessage
        msgs = self.session_messages
        if msgs is None:
            return
        turn_number = AskAgentLoop._count_completed_turns(msgs) + 1
        self._multi_turn_number = turn_number
        msgs.append(LLMMessage("user", f"[turn:{turn_number}:start]\n{question}"))

    def _multi_end_turn(self, aggregated: "AssistantResponse") -> None:
        """Append aggregated answer + turn-end marker to the session stream."""
        from dbaide.llm import LLMMessage
        msgs = self.session_messages
        if msgs is None:
            return
        turn_number = getattr(self, "_multi_turn_number", 0) or 0
        answer = getattr(aggregated, "answer", "") or ""
        if answer:
            msgs.append(LLMMessage("assistant", answer))
        msgs.append(LLMMessage("user", f"[turn:{turn_number}:end] Answer delivered."))

    def _aggregate(
        self,
        question: str,
        results: list[tuple[Any, AssistantResponse]],
        *,
        disclosures_before: list[str] | None = None,
    ) -> AssistantResponse:
        sections: list[str] = []
        warnings: list[str] = []
        disclosures: list[str] = []
        all_charts: list[dict[str, Any]] = []
        all_executed_sqls: list[dict[str, Any]] = []
        primary: AssistantResponse | None = None
        answer_language = detect_user_language(question)
        empty_answer = "（无回答）" if answer_language == "zh" else "(no answer)"
        for idx, (intent, resp) in enumerate(results, start=1):
            answer_text = resp.answer or empty_answer
            charts, chart_id_map = merge_chart_specs(all_charts, resp.charts)
            if chart_id_map:
                answer_text = remap_chart_refs(answer_text, chart_id_map)
            sections.append(
                f"## {idx}. {intent.label_for(answer_language)} — {intent.text}\n\n"
                f"{answer_text}"
            )
            warnings.extend(resp.warnings or [])
            disclosures.extend(resp.disclosures or [])
            all_charts.extend(charts)
            for item in (resp.executed_sqls or []):
                _append_aggregate_execution(all_executed_sqls, item)
            if primary is None and resp.result is not None:
                primary = resp
        if not disclosures and disclosures_before is not None:
            disclosures = self._new_disclosures(disclosures_before)
        answer = "\n\n".join(sections)
        return AssistantResponse(
            answer=answer,
            sql=(primary.sql if primary else ""),
            result=(primary.result if primary else None),
            disclosures=disclosures,
            warnings=warnings,
            charts=all_charts or None,
            executed_sqls=all_executed_sqls or None,
        )

    # ─── Schema ─────────────────────────────────────────────────────────────

    def _discover(
        self,
        question: str,
        *,
        parent: str = "",
        column_detail: bool = True,
        scope: dict[str, Any] | None = None,
    ):
        agent = ProgressiveSchemaAgent(
            self.llm,
            self.asset_store,
            self.instance,
            fingerprint=self.connection_fingerprint,
        )
        progress_cb = self.progress if parent else None
        # Prioritise the user's pinned scope on the first discovery; broaden afterwards
        # so the agent can recover if the pinned tables don't actually answer the
        # question (a permanently-scoped run would otherwise never see the right table).
        effective_scope = scope if scope is not None else (
            self.schema_scope if (self.schema_scope and not self.run_state.scope_used) else {}
        )
        if effective_scope and scope is None:
            self.run_state.scope_used = True
        discovery = agent.discover(
            question,
            schema_tools=self.schema,
            progress=progress_cb,
            parent=parent,
            column_detail=column_detail,
            scope=effective_scope,
        )
        # Fold the matching user note onto each hit (db/table/column) so the note
        # travels with its object through every downstream consumer.
        from dbaide.agent.schema_context import attach_notes_to_hits
        attach_notes_to_hits(self, discovery)
        return discovery

    def _new_disclosures(self, before: list[str]) -> list[str]:
        return self.session.disclosure.events[len(before):]

    def _seed_session_disclosure(self) -> None:
        """Re-disclose tables surfaced in earlier turns of this chat session into
        the live DisclosureContext. Cheap (in-memory records, no DB round-trips):
        the schema guard only needs the table to be known, and the agent already
        carries column detail in the conversation. Idempotent across sub-intents."""
        if not self.session_turns:
            return
        dc = self.session.disclosure
        items: list[tuple] = []
        for turn in self.session_turns:
            for key in (turn.get("disclosed_tables") or []):
                key = str(key).strip()
                if not key:
                    continue
                db, _sep, table = key.rpartition(".")
                items.append((db, table or key))
        if items:
            if not dc.instance:
                dc.set_instance(self.instance)
            dc.redisclose(items, source="prior turns")

    def _seed_session_memory(self) -> None:
        """Re-seed verified facts + excluded paths from earlier turns of this chat
        session into the current run's memory, so they persist across turns and
        survive message compression (the turn prompt re-injects them)."""
        if not self.session_turns:
            return
        mem = self.run_state.memory
        for turn in self.session_turns:
            for fact in (turn.get("verified_facts") or []):
                if str(fact).strip():
                    mem.mark_verified(str(fact))
            for ep in (turn.get("excluded_paths") or []):
                if isinstance(ep, dict) and str(ep.get("target") or "").strip():
                    mem.add_exclusion(
                        str(ep.get("target") or ""), str(ep.get("reason") or ""),
                        evidence_ref=str(ep.get("evidence_ref") or ""),
                        source_priority=str(ep.get("source_priority") or "evidence"),
                    )





def format_inspect(info: dict) -> str:
    lines = [f"Table: {info.get('table', '?')}", "Columns:"]
    for col in info.get("columns") or []:
        flags = []
        if col.primary_key:
            flags.append("PK")
        if col.indexed:
            flags.append("indexed")
        flag_text = f" [{' '.join(flags)}]" if flags else ""
        comment = f" - {col.comment}" if col.comment else ""
        lines.append(f"- {col.name}: {col.data_type}{flag_text}{comment}")
    fks = info.get("foreign_keys") or []
    if fks:
        lines.append("Foreign Keys:")
        for fk in fks:
            lines.append(f"- {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}")
    return "\n".join(lines)


def _sub_intent_from_dict(data: Any):
    from dbaide.agent.intent import INTENT_TYPES, SubIntent

    data = data if isinstance(data, dict) else {}
    itype = str(data.get("type") or "other").strip().lower()
    if itype not in INTENT_TYPES:
        itype = "other"
    return SubIntent(
        id=str(data.get("id") or "i1"),
        type=itype,
        text=str(data.get("text") or ""),
        language=normalize(data.get("language") or "en"),
    )


def _append_aggregate_execution(bucket: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        return
    entry = dict(item)
    index = len(bucket) + 1
    entry["index"] = index
    artifact_id = str(entry.get("artifact_id") or "")
    if not artifact_id or artifact_id.startswith("sql:"):
        entry["artifact_id"] = f"sql:{index}"
    bucket.append(entry)
