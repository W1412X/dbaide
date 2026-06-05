"""Unified Ask orchestrator — LLM routing, progressive schema, SQL, risk control."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.agent.answerer import AnswerFormatter
from dbaide.agent.controllers import ErrorRouter, ResultInterpreter, RiskController
from dbaide.agent.progress_events import progress_event
from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
from dbaide.agent.sql_writer import SQLWriter
from dbaide.joins import JoinCatalogStore
from dbaide.annotations import AnnotationStore
from dbaide.assets import AssetStore
from dbaide.core.result import ExecutionPolicy
from dbaide.i18n import t as _i18n_t
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import AssistantResponse, ColumnInfo, TaskType
from dbaide.session import Session
from dbaide.tools import DiagnoseTools, ProfileTools, QueryTools, SchemaTools

logger = logging.getLogger("dbaide.orchestrator")


@dataclass(slots=True)
class AgentStep:
    name: str
    status: str = "pending"
    detail: str = ""
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentContext:
    question: str = ""
    database: str = ""
    task: TaskType | None = None
    table: str = ""
    columns: list[str] = field(default_factory=list)
    sql: str = ""
    error: str = ""
    steps: list[AgentStep] = field(default_factory=list)


class AskOrchestrator:
    """Codex-style Ask agent: route → discover → act → validate → execute (with risk gate)."""

    MAX_SQL_RETRIES = 2  # default; the effective value comes from session.agent_sql_retries

    def __init__(
        self,
        adapter: DatabaseAdapter,
        session: Session,
        llm: LLMClient | None = None,
        *,
        asset_store: AssetStore | None = None,
        join_catalog: JoinCatalogStore | None = None,
        annotations: AnnotationStore | None = None,
        execution_policy: ExecutionPolicy = ExecutionPolicy.SAFE_AUTO,
        progress: Callable[[Any], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.instance = session.connection.name
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
        self.asset_store = asset_store or AssetStore()
        self.join_catalog = join_catalog or JoinCatalogStore()
        self.annotations = annotations or AnnotationStore()
        self.execution_policy = execution_policy
        self.progress = progress or (lambda _msg: None)
        # User-pinned schema scope (set by the workflow from composer attachments).
        self.schema_scope: dict[str, Any] = {}
        # Stream the final answer token-by-token (set by the workflow from config).
        self.stream_answers: bool = False

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

        self.sql_writer = SQLWriter(llm, dialect=adapter.dialect)
        self.formatter = AnswerFormatter()
        self.risk = RiskController()
        self.error_router = ErrorRouter()
        self.interpreter = ResultInterpreter()
        self._reset_loop_state("", "", False)

    def _reset_loop_state(self, question: str, database: str, execute: bool) -> None:
        self._loop_question = question
        self._loop_database = database
        self._loop_execute_allowed = execute
        self._loop_discovery = None
        self._loop_table = ""
        self._loop_table_database = database
        self._loop_columns: list[ColumnInfo] = []
        self._loop_schemas: dict[str, list[ColumnInfo]] = {}
        self._loop_schema_db: dict[str, str] = {}
        self._loop_relations: list[dict[str, Any]] = []
        self._loop_resolved_schema = None  # ResolvedSchema from the schema linker (minimal-necessary)
        self._loop_trace_node = ""  # node id of the tool step currently running (for nested traces)
        self._loop_sql = ""
        self._loop_sql_rationale = ""
        self._loop_sql_confidence = None  # None = no SQL generated yet (neutral); a float is the writer's real confidence
        self._loop_query_result = None
        self._loop_answer = ""
        self._loop_sql_feedback = ""
        self._loop_pending_question = ""
        self._loop_pending_options = []
        self._loop_pending_questions: list[dict[str, Any]] = []
        self._loop_fail_reason = ""
        # The pinned scope (attachments) prioritises the FIRST discovery only; a later
        # discovery in the same run broadens, so a wrong/insufficient pin can't trap
        # the agent into searching only the attached scope forever.
        self._scope_used = False
        # Business-criteria (口径) clarification: confirmed criteria injected into SQL,
        # and the questions currently awaiting a user reply (paired with it on resume).
        self._loop_clarifications: list[str] = []
        self._loop_clarify_questions = ""

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
            resp = self._run_single(question, database=database, execute=execute,
                                    resume_state=resume_state, user_reply=user_reply)
            # If the pause happened inside a multi-intent plan, resume the WHOLE plan
            # (the paused intent + any not-yet-run ones) rather than dropping the rest.
            if multi is not None:
                return self._continue_multi(multi, resp, database=database, execute=execute)
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
        # Intent decomposition runs before the tool loop, so its LLM call isn't in any
        # tool step's capture window — surface it as its own debug-trace step.
        if rec is not None:
            intent_calls = rec.since(intent_start)
            if intent_calls:
                ev = progress_event(stage="intent", title="Decompose intent",
                                    status="completed", kind="tool", step=0)
                ev["llm_calls"] = intent_calls
                self.progress(ev)
        if len(intents) > 1:
            return self._run_multi(question, intents, database=database, execute=execute)
        return self._run_single(question, database=database, execute=execute)

    def _run_single(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
        trace_parent: str = "",
    ) -> AssistantResponse:
        self.error_router.reset()
        self._loop_fail_reason = ""  # fresh per run (never carry a stale reason)
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
            )
        except Exception as exc:
            logger.warning("agent_loop_failed: %s", exc, exc_info=True)
            self._loop_fail_reason = f"exception: {exc}"
            return AssistantResponse(
                answer=_i18n_t("agent.loop_failed"),
                disclosures=self._new_disclosures(disclosures),
                warnings=[_i18n_t("agent.loop_failed_reason", reason=str(exc))],
            )

    def _run_multi(self, question: str, intents, *, database: str, execute: bool) -> AssistantResponse:
        """Run independent sub-intents in turn and aggregate. Each sub-intent keeps a
        self-contained answer + result, and its steps nest under an intent node in
        the trace so the user sees every sub-intent's execution."""
        from dbaide.agent.progress_events import progress_event

        self.progress(progress_event(
            stage="decompose", title=f"Decomposed into {len(intents)} sub-intents",
            status="completed", kind="phase", node_id="intent:plan",
        ))
        results: list[tuple[Any, AssistantResponse]] = []
        for idx, intent in enumerate(intents, start=1):
            node_id = f"intent:{intent.id}"
            self.progress(progress_event(
                stage="intent", title=f"{idx}. {intent.label}: {intent.text}",
                status="running", kind="phase", node_id=node_id,
            ))
            resp = self._run_single(intent.text, database=database, execute=execute, trace_parent=node_id)
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
                    resp.resume_state, question, results, intent, intents[idx:]
                )
                return resp
            results.append((intent, resp))
        return self._aggregate(question, results)

    @staticmethod
    def _ser_intent(intent) -> dict[str, Any]:
        return {"id": intent.id, "type": intent.type, "text": intent.text}

    def _attach_multi(self, resume_state, question, done_results, paused_intent, remaining) -> dict[str, Any]:
        """Fold the multi-intent plan into the paused intent's resume snapshot."""
        state = dict(resume_state or {})
        state["multi"] = {
            "question": question,
            "done": [
                {"intent": self._ser_intent(it), "answer": rp.answer, "sql": rp.sql}
                for it, rp in done_results
            ],
            "paused": self._ser_intent(paused_intent),
            "remaining": [self._ser_intent(it) for it in remaining],
        }
        return state

    def _continue_multi(self, multi: dict[str, Any], paused_resp: AssistantResponse,
                        *, database: str, execute: bool) -> AssistantResponse:
        """After a paused sub-intent resumes, finish it + run the remaining sub-intents."""
        from dbaide.agent.intent import SubIntent

        question = str(multi.get("question") or "")
        done: list[tuple[Any, AssistantResponse]] = [
            (SubIntent(**d["intent"]), AssistantResponse(answer=d.get("answer") or "", sql=d.get("sql") or ""))
            for d in (multi.get("done") or [])
        ]
        paused_intent = SubIntent(**multi["paused"])

        # The resumed intent paused AGAIN → re-attach the plan and keep waiting.
        if getattr(paused_resp, "status", "completed") == "wait_user":
            paused_resp.resume_state = self._attach_multi(
                paused_resp.resume_state, question, done, paused_intent,
                [SubIntent(**r) for r in (multi.get("remaining") or [])],
            )
            return paused_resp

        results = done + [(paused_intent, paused_resp)]
        remaining = [SubIntent(**r) for r in (multi.get("remaining") or [])]
        for i, intent in enumerate(remaining):
            resp = self._run_single(intent.text, database=database, execute=execute,
                                    trace_parent=f"intent:{intent.id}")
            if getattr(resp, "status", "completed") == "wait_user":
                resp.resume_state = self._attach_multi(
                    resp.resume_state, question, results, intent, remaining[i + 1:]
                )
                return resp
            results.append((intent, resp))
        return self._aggregate(question, results)

    def _aggregate(self, question: str, results: list[tuple[Any, AssistantResponse]]) -> AssistantResponse:
        sections: list[str] = []
        warnings: list[str] = []
        primary: AssistantResponse | None = None
        for idx, (intent, resp) in enumerate(results, start=1):
            sections.append(f"## {idx}. {intent.label} — {intent.text}\n\n{resp.answer or '(no answer)'}")
            warnings.extend(resp.warnings or [])
            if primary is None and resp.result is not None:
                primary = resp  # keep the first concrete result for the SQL tab
        answer = "\n\n".join(sections)
        return AssistantResponse(
            answer=answer,
            sql=(primary.sql if primary else ""),
            result=(primary.result if primary else None),
            disclosures=self._new_disclosures(list(self.session.disclosure.events)),
            warnings=warnings,
        )

    # ─── Schema ─────────────────────────────────────────────────────────────

    def _discover(self, question: str, *, parent: str = "", column_detail: bool = True):
        agent = ProgressiveSchemaAgent(self.llm, self.asset_store, self.instance)
        progress_cb = self.progress if parent else None
        # Prioritise the user's pinned scope on the first discovery; broaden afterwards
        # so the agent can recover if the pinned tables don't actually answer the
        # question (a permanently-scoped run would otherwise never see the right table).
        scope = self.schema_scope if (self.schema_scope and not getattr(self, "_scope_used", False)) else {}
        if scope:
            self._scope_used = True
        discovery = agent.discover(
            question,
            schema_tools=self.schema,
            progress=progress_cb,
            parent=parent,
            column_detail=column_detail,
            scope=scope,
        )
        # Fold the matching user note onto each hit (db/table/column) so the note
        # travels with its object through every downstream consumer.
        from dbaide.agent.schema_context import attach_notes_to_hits
        attach_notes_to_hits(self, discovery)
        return discovery

    # ─── Profile ────────────────────────────────────────────────────────────

    # ─── SQL diagnose / rewrite ─────────────────────────────────────────────

    # ─── Data query ─────────────────────────────────────────────────────────

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _new_disclosures(self, before: list[str]) -> list[str]:
        return self.session.disclosure.events[len(before):]







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
