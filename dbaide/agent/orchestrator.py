"""Unified Ask orchestrator — LLM routing, progressive schema, SQL, risk control."""

from __future__ import annotations

import logging
from typing import Any, Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.agent.answerer import AnswerFormatter
from dbaide.agent.controllers import ResultInterpreter, RiskController
from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
from dbaide.agent.run_state import RunState
from dbaide.agent.sql_writer import SQLWriter
from dbaide.joins import JoinCatalogStore
from dbaide.annotations import AnnotationStore
from dbaide.assets import AssetStore
from dbaide.connection_identity import connection_fingerprint
from dbaide.core.cancellation import CancelledError
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
        self.tool_allowlist: set[str] | None = None

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
        # Nothing is carried across turns as authoritative state. Prior turns'
        # criteria / facts / explored tables live in the chat history (compression
        # preserves them into the per-turn summaries); the model attends to them by
        # relevance. There is no schema-disclosure gate to keep in sync — table
        # existence is proven by the DB at execution time, and any per-connection
        # table scope (TableScopeGuard) is stateless. So a fresh run starts clean.

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

        return self._run_single(
            question,
            database=database,
            execute=execute,
            resume_state=resume_state,
            user_reply=user_reply,
            answer_language=detect_user_language(question),
        )

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
    ) -> AssistantResponse:
        self.run_state.fail_reason = ""  # fresh per run (never carry a stale reason)
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
                session_messages=self.session_messages,
            )
        except CancelledError:
            # User cancellation is NOT a failure: let it propagate so the workflow maps
            # it to a CANCELLED status (and the UI shows "cancelled", not a loop error).
            # CancelledError subclasses Exception, so it must be re-raised before the
            # generic handler below swallows it.
            raise
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
