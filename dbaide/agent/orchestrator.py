"""Unified Ask orchestrator — LLM routing, progressive schema, SQL, risk control."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.agent.answerer import AnswerFormatter
from dbaide.agent.controllers import ErrorRouter, ResultInterpreter, RiskController
from dbaide.agent.schema_context import (
    collect_relations,
    disclosed_table_keys,
    join_confidence_for_sql,
    merge_sql_context,
    table_targets_from_discovery,
    validation_feedback,
)
from dbaide.agent.progress_events import progress_event
from dbaide.agent.progressive_schema import ModelRequiredError, ProgressiveSchemaAgent
from dbaide.agent.router import TaskRouter
from dbaide.agent.sql_writer import SQLWriter
from dbaide.joins import JoinCatalogStore
from dbaide.assets import AssetStore
from dbaide.core.errors import DBAideError, ErrorCode
from dbaide.core.result import ExecutionPolicy, ValidationReport
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
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
        execution_policy: ExecutionPolicy = ExecutionPolicy.SAFE_AUTO,
        progress: Callable[[Any], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.instance = session.connection.name
        self.llm = llm or NullLLMClient()
        self.asset_store = asset_store or AssetStore()
        self.join_catalog = join_catalog or JoinCatalogStore()
        self.execution_policy = execution_policy
        self.progress = progress or (lambda _msg: None)

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

        self.router = TaskRouter(llm)
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
        self._loop_sql_confidence = 0.0
        self._loop_query_result = None
        self._loop_answer = ""
        self._loop_sql_feedback = ""
        self._loop_pending_question = ""
        self._loop_pending_options = []
        self._loop_fail_reason = ""
        # Rendered memory (worked examples from effective past questions) injected
        # into the loop prompt and SQL generation context.
        self._loop_memory = getattr(self, "_run_memory", "")

    def run(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        resume_state: dict[str, Any] | None = None,
        user_reply: str = "",
        memory: str = "",
    ) -> AssistantResponse:
        self._run_memory = memory
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
            return self._run_single(question, database=database, execute=execute,
                                    resume_state=resume_state, user_reply=user_reply)

        from dbaide.agent.intent import IntentDecomposer
        try:
            intents = IntentDecomposer(self.llm).decompose(question)
        except Exception as exc:
            logger.warning("intent_decompose_failed: %s", exc)
            intents = []
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
        disclosures = list(self.session.disclosure.events)

        try:
            from dbaide.agent.loop import AskAgentLoop

            loop_response = AskAgentLoop(self, progress=self.progress).run(
                question,
                database=database,
                execute=execute,
                disclosures_before=disclosures,
                resume_state=resume_state,
                user_reply=user_reply,
                trace_parent=trace_parent,
            )
            if loop_response is not None:
                return loop_response
        except Exception as exc:
            logger.warning("agent_loop_failed: %s", exc, exc_info=True)
            self._loop_fail_reason = self._loop_fail_reason or f"exception: {exc}"

        logger.info("agent_loop_fallback_to_staged question=%s", question[:80])
        staged = self._run_staged(question, database=database, execute=execute, disclosures=disclosures)
        reason = (self._loop_fail_reason or "unknown").strip()
        staged.warnings.append(f"Tool loop unavailable ({reason}); using staged pipeline.")
        return staged

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
            results.append((intent, resp))
            # If a sub-intent pauses for the user, surface that immediately.
            if getattr(resp, "status", "completed") == "wait_user":
                return resp
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

    def _run_staged(self, question: str, *, database: str = "", execute: bool, disclosures: list[str]) -> AssistantResponse:

        start = time.perf_counter()
        ctx = AgentContext(question=question, database=database)
        self._step(ctx, "disclose", "Disclosing schema context…")
        self.schema.disclose_instance()
        databases = self.schema.list_databases()
        active_database = database or (databases[0] if len(databases) == 1 else "")
        ctx.database = active_database

        self._step(ctx, "route", "Classifying intent (LLM)…")
        try:
            task = self.router.route(question)
        except ModelRequiredError as exc:
            return AssistantResponse(answer=str(exc), warnings=["Model required"])
        except RuntimeError as exc:
            return AssistantResponse(answer=f"Routing failed: {exc}", warnings=["Routing error"])
        ctx.task = task
        logger.info("ask task=%s db=%s question=%s", task.value, active_database, question[:80])

        if task in (TaskType.SCHEMA_EXPLORE, TaskType.UNKNOWN):
            return self._handle_schema_explore(ctx, disclosures)

        if task == TaskType.DATA_PROFILE:
            return self._handle_data_profile(ctx, disclosures, active_database)

        if task == TaskType.SQL_DIAGNOSE and _contains_sql(question):
            return self._handle_sql_diagnose(ctx, disclosures, active_database)

        if task == TaskType.SQL_REWRITE and _contains_sql(question):
            return self._handle_sql_rewrite(ctx, disclosures, active_database, execute)

        return self._handle_data_query(ctx, disclosures, active_database, execute, start)

    # ─── Schema ─────────────────────────────────────────────────────────────

    def _handle_schema_explore(self, ctx: AgentContext, disclosures: list[str]) -> AssistantResponse:
        self._step(ctx, "discover", "Progressive schema discovery (LLM)…")
        try:
            discovery = self._discover(ctx.question)
            for note in discovery.trace:
                self._step(ctx, "discover", note)
            agent = ProgressiveSchemaAgent(self.llm, self.asset_store, self.instance)
            self._step(ctx, "synthesize", "Synthesizing answer…")
            answer = agent.synthesize_answer(ctx.question, discovery)
            self._step(ctx, "done", f"Found {len(discovery.hits)} relevant object(s)")
            return AssistantResponse(
                answer=answer,
                disclosures=self._new_disclosures(disclosures),
                warnings=self._collect_warnings(ctx),
            )
        except ModelRequiredError as exc:
            return AssistantResponse(answer=str(exc), warnings=["Model required"])
        except Exception as exc:
            logger.exception("schema_discovery_failed")
            return AssistantResponse(
                answer=f"Schema discovery failed: {exc}",
                disclosures=self._new_disclosures(disclosures),
                warnings=self._collect_warnings(ctx),
            )

    def _discover(self, question: str, *, parent: str = "", column_detail: bool = True):
        agent = ProgressiveSchemaAgent(self.llm, self.asset_store, self.instance)
        progress_cb = self.progress if parent else None
        return agent.discover(
            question,
            schema_tools=self.schema,
            progress=progress_cb,
            parent=parent,
            column_detail=column_detail,
        )

    def _pick_table(self, question: str, active_database: str) -> tuple[str, str]:
        try:
            discovery = self._discover(question)
            agent = ProgressiveSchemaAgent(self.llm, self.asset_store, self.instance)
            table_hits = [h for h in discovery.hits if h.kind == "table" and h.table]
            if len(table_hits) == 1:
                hit = table_hits[0]
                return hit.table, hit.database or active_database
            if len(table_hits) > 1:
                items = [
                    {"index": i, "name": h.table, "summary": _brief(h.summary or h.path)}
                    for i, h in enumerate(table_hits[:12])
                ]
                kept = agent._filter_indices(  # noqa: SLF001
                    question,
                    level="table",
                    items=items,
                    context=f"discovered tables in {active_database or 'default'}",
                )
                if kept:
                    hit = table_hits[kept[0]]
                    return hit.table, hit.database or active_database
            table, table_database = agent.top_table(discovery)
            if table:
                return table, table_database or active_database
        except Exception as exc:
            logger.warning("progressive_table_pick_failed: %s", exc)

        return self._llm_pick_table(question, active_database)

    def _llm_pick_table(self, question: str, active_database: str) -> tuple[str, str]:
        """Last-resort table selection via LLM over live table names (no keyword matcher)."""
        tables = self.schema.list_tables(database=active_database)
        if not tables:
            return "", active_database
        items = [{"index": i, "name": t.name, "summary": _brief(str(t.comment or ""))} for i, t in enumerate(tables[:40])]
        agent = ProgressiveSchemaAgent(self.llm, self.asset_store, self.instance)
        kept = agent._filter_indices(  # noqa: SLF001 — shared LLM filter primitive
            question,
            level="table",
            items=items,
            context=f"database={active_database or 'default'}",
        )
        if kept:
            picked = tables[kept[0]]
            return picked.name, active_database or picked.schema
        return "", active_database

    # ─── Profile ────────────────────────────────────────────────────────────

    def _handle_data_profile(self, ctx: AgentContext, disclosures: list[str], active_database: str) -> AssistantResponse:
        self._step(ctx, "discover", "Finding table to profile…")
        table, table_database = self._pick_table(ctx.question, active_database)
        if not table:
            return AssistantResponse(
                answer="No suitable table found for profiling.",
                disclosures=self._new_disclosures(disclosures),
                warnings=self._collect_warnings(ctx),
            )
        ctx.table = table
        columns = self.schema.describe_table(table, database=table_database)
        ctx.columns = [c.name for c in columns[:8]]
        profiles = self.profile.profile_table(table, ctx.columns, database=table_database)
        return AssistantResponse(
            answer=self.formatter.profiles(profiles),
            disclosures=self._new_disclosures(disclosures),
            warnings=self._collect_warnings(ctx),
        )

    # ─── SQL diagnose / rewrite ─────────────────────────────────────────────

    def _handle_sql_diagnose(self, ctx: AgentContext, disclosures: list[str], active_database: str) -> AssistantResponse:
        ctx.sql = _extract_sql(ctx.question)
        report = self.diagnose.diagnose_sql(ctx.sql, database=active_database)
        return AssistantResponse(
            answer=_format_diagnose(report),
            disclosures=self._new_disclosures(disclosures),
            warnings=self._collect_warnings(ctx),
        )

    def _handle_sql_rewrite(
        self, ctx: AgentContext, disclosures: list[str], active_database: str, execute: bool,
    ) -> AssistantResponse:
        original = _extract_sql(ctx.question)
        table, table_database = self._pick_table(ctx.question, active_database)
        if not table:
            return AssistantResponse(answer="Could not determine target table for SQL rewrite.", warnings=["No table"])
        columns = self.schema.describe_table(table, database=table_database)
        prompt = f"Rewrite this SQL for the question context.\nOriginal SQL:\n{original}\n\nQuestion:\n{ctx.question}"
        draft = self.sql_writer.write(prompt, table, columns, context=self.session.disclosure.summary())
        validation = self.query.validate_sql(draft.sql, add_limit=True)
        if not validation.ok:
            issues = "\n".join(f"- {i.message}" for i in validation.issues)
            return AssistantResponse(answer=f"Rewritten SQL failed validation:\n{issues}", sql=draft.sql)
        return self._finalize_sql(ctx, disclosures, draft, validation, table_database, execute)

    # ─── Data query ─────────────────────────────────────────────────────────

    def _handle_data_query(
        self, ctx: AgentContext, disclosures: list[str], active_database: str,
        execute: bool, start: float,
    ) -> AssistantResponse:
        discovery = None
        targets: list[tuple[str, str]] = []
        loop_tables = disclosed_table_keys(self)

        max_tables = self.session.agent_max_disclosed_tables
        if self._loop_discovery and self._loop_discovery.hits:
            discovery = self._loop_discovery
            self._step(ctx, "discover", f"Reusing tool-loop discovery ({len(discovery.hits)} hit(s))")
            targets = table_targets_from_discovery(discovery, active_database, limit=max_tables)
        elif loop_tables:
            self._step(ctx, "discover", f"Reusing {len(loop_tables)} table(s) from tool loop")
            targets = loop_tables
        else:
            self._step(ctx, "discover", "Progressive schema discovery…")
            discovery = self._discover(ctx.question)
            targets = table_targets_from_discovery(discovery, active_database, limit=max_tables)

        if not targets:
            table, table_database = self._pick_table(ctx.question, active_database)
            if table:
                targets = [(table_database, table)]
        if not targets:
            return AssistantResponse(
                answer=(
                    "No suitable table found.\n\n"
                    "Try building assets, naming a table in your question, or asking a schema question first."
                ),
                disclosures=self._new_disclosures(disclosures),
                warnings=self._collect_warnings(ctx),
            )

        disclosed: list[tuple[str, str, list[ColumnInfo]]] = []
        for database, table in targets:
            schema_key = f"{database}.{table}" if database else table
            cached = self._loop_schemas.get(schema_key)
            if cached is None:
                for key, columns in self._loop_schemas.items():
                    if key == table or key.endswith(f".{table}"):
                        cached = columns
                        database = self._loop_schema_db.get(key, database)
                        break
            if cached is not None:
                self._step(ctx, "describe", f"Reusing schema for {database}.{table}")
                disclosed.append((database, table, cached))
                continue
            self._step(ctx, "describe", f"Describing table {database}.{table}…")
            columns = self.schema.describe_table(table, database=database)
            disclosed.append((database, table, columns))
        ctx.table = disclosed[0][1]
        ctx.columns = [c.name for c in disclosed[0][2]]
        table_database = disclosed[0][0]

        relations = self._loop_relations or collect_relations(
            self,
            targets,
            question=ctx.question,
            disclosed_schemas=disclosed,
            parent="staged",
        )
        self._loop_relations = relations
        sql_context = merge_sql_context(self.session.disclosure.summary(), relations)
        if getattr(self, "_loop_memory", ""):
            sql_context["examples"] = self._loop_memory  # worked examples from memory

        draft = None
        validation = None
        feedback = ""
        max_retries = self.session.agent_sql_retries
        for attempt in range(max_retries + 1):
            self._step(ctx, "generate", f"Generating SQL (attempt {attempt + 1})…")
            if len(disclosed) == 1:
                database, table, columns = disclosed[0]
                draft = self.sql_writer.write(
                    ctx.question,
                    table,
                    columns,
                    context=sql_context,
                    feedback=feedback,
                )
            else:
                draft = self.sql_writer.write(
                    ctx.question,
                    disclosed_schemas=disclosed,
                    context=sql_context,
                    feedback=feedback,
                )
            ctx.sql = draft.sql
            validation = self.query.validate_sql(draft.sql, add_limit=True)
            if validation.ok:
                break
            issues = [issue.message for issue in validation.issues]
            ctx.error = "; ".join(issues)
            feedback = validation_feedback(issues)
            if attempt < max_retries:
                self._step(ctx, "retry", f"Validation failed: {feedback}. Retrying…")
                continue
            return AssistantResponse(
                answer="Generated SQL failed validation:\n" + "\n".join(f"- {i.message}" for i in validation.issues),
                sql=draft.sql,
                disclosures=self._new_disclosures(disclosures),
                warnings=self._collect_warnings(ctx),
            )

        assert draft is not None and validation is not None
        return self._finalize_sql(ctx, disclosures, draft, validation, table_database, execute, start)

    def _finalize_sql(
        self,
        ctx: AgentContext,
        disclosures: list[str],
        draft,
        validation,
        table_database: str,
        execute: bool,
        start: float = 0.0,
    ) -> AssistantResponse:
        normalized_sql = validation.normalized_sql
        ctx.sql = normalized_sql
        tables_in_sql = _extract_tables(normalized_sql)
        validation_report = self.query.validate_sql_report(normalized_sql, add_limit=False)
        if not validation_report.ok:
            validation_report = ValidationReport(
                ok=validation.ok,
                normalized_sql=normalized_sql,
                issues=[i.message for i in validation.issues],
                warnings=[],
                risk_level="rejected",
                requires_confirmation=False,
            )
        risk = self.risk.decide(
            policy=self.execution_policy,
            validation=validation_report,
            plan_confidence=float(draft.confidence),
            table_count=max(1, len(tables_in_sql)),
            has_joins=" join " in normalized_sql.lower(),
            join_confidence=join_confidence_for_sql(self._loop_relations, normalized_sql)
            if " join " in normalized_sql.lower()
            else 1.0,
        )
        self._step(ctx, "risk", f"Risk decision: {risk.action} ({risk.reason})")

        if risk.action == "reject":
            return AssistantResponse(
                answer=f"SQL rejected: {risk.reason}",
                sql=normalized_sql,
                disclosures=self._new_disclosures(disclosures),
                warnings=[risk.reason],
            )

        if not execute or risk.action in {"generate_only", "confirm"}:
            note = risk.reason if risk.action == "confirm" else "Execution disabled by policy."
            self._step(ctx, "done", "SQL generated (not executed)")
            return AssistantResponse(
                answer=f"SQL:\n```sql\n{normalized_sql}\n```\n\nRationale:\n{draft.rationale}\n\n_{note}_",
                sql=normalized_sql,
                disclosures=self._new_disclosures(disclosures),
                warnings=[note] if note else [],
            )

        self._step(ctx, "execute", "Executing SQL…")
        try:
            result = self.query.execute_sql(
                normalized_sql, database=table_database, limit=self.session.default_limit,
            )
        except Exception as exc:
            ctx.error = str(exc)
            repair = self.error_router.route(
                DBAideError(
                    code=ErrorCode.SQL_EXECUTION_FAILED,
                    stage="execute",
                    message=str(exc),
                    retryable=True,
                ),
                "execute",
            )
            if repair.value == "rerender_sql":
                corrected = self._attempt_self_correction(ctx, columns := self.schema.describe_table(ctx.table, database=table_database), table_database)
                if corrected:
                    # The corrected query is what actually produced `result`; the
                    # answer must reflect that SQL/rationale, not the failed original.
                    result, normalized_sql, draft = corrected
                else:
                    return AssistantResponse(
                        answer=f"SQL execution failed:\n{exc}\n\nSQL:\n```sql\n{normalized_sql}\n```",
                        sql=normalized_sql,
                        disclosures=self._new_disclosures(disclosures),
                        warnings=self._collect_warnings(ctx),
                    )
            else:
                return AssistantResponse(
                    answer=f"SQL execution failed:\n{exc}",
                    sql=normalized_sql,
                    disclosures=self._new_disclosures(disclosures),
                    warnings=self._collect_warnings(ctx),
                )

        elapsed = (time.perf_counter() - start) * 1000 if start else result.elapsed_ms
        self._step(ctx, "done", f"Query returned {result.row_count} rows in {elapsed:.0f}ms")
        warnings = list(validation_report.warnings)
        if risk.action == "confirm":
            warnings.append(risk.reason)
        interpretation = self.interpreter.interpret(
            question=ctx.question,
            sql=normalized_sql,
            row_count=result.row_count,
            columns=result.columns,
            elapsed_ms=result.elapsed_ms,
            truncated=result.truncated,
            warnings=warnings,
        )
        answer = self.formatter.query_result(
            result,
            rationale=draft.rationale,
            interpretation=interpretation,
        )
        return AssistantResponse(
            answer=answer,
            sql=normalized_sql,
            result=result,
            disclosures=self._new_disclosures(disclosures),
            warnings=self._collect_warnings(ctx) + warnings,
        )

    def _attempt_self_correction(self, ctx: AgentContext, columns: list[ColumnInfo], database: str):
        error_hint = f"The previous SQL failed with: {ctx.error}\nOriginal question: {ctx.question}"
        try:
            draft = self.sql_writer.write(error_hint, ctx.table, columns, context=self.session.disclosure.summary())
            validation = self.query.validate_sql(draft.sql, add_limit=True)
            if validation.ok:
                self._step(ctx, "corrected", "Self-correction successful")
                result = self.query.execute_sql(
                    validation.normalized_sql, database=database, limit=self.session.default_limit,
                )
                return result, validation.normalized_sql, draft
        except Exception as exc:
            logger.debug("self_correction_failed: %s", exc)
        return None

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _step(self, ctx: AgentContext, name: str, detail: str) -> None:
        ctx.steps.append(AgentStep(name=name, status="done", detail=detail))
        self.progress(
            progress_event(stage=name, title=detail, status="completed", kind="decision"),
        )

    def _collect_warnings(self, ctx: AgentContext) -> list[str]:
        return [f"Error: {ctx.error}"] if ctx.error else []

    def _new_disclosures(self, before: list[str]) -> list[str]:
        return self.session.disclosure.events[len(before):]


def _brief(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def _contains_sql(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\bselect\s+", lowered)) or bool(re.search(r"\bwith\s+\w+\s+as\s*\(", lowered))


def _extract_sql(text: str) -> str:
    lowered = text.lower()
    for marker in ("select ", "with "):
        idx = lowered.find(marker)
        if idx >= 0:
            rest = text[idx:].strip().rstrip("?").rstrip("。")
            return rest
    return text.strip()


def _extract_tables(sql: str) -> list[str]:
    tokens = sql.replace("\n", " ").replace(",", " ").split()
    tables: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"from", "join"}:
            table = tokens[index + 1].strip('"`[]')
            if table and table.lower() not in {"select", "where"} and table not in tables:
                tables.append(table)
    return tables


def _format_diagnose(report: dict) -> str:
    if not report.get("ok"):
        return "Diagnosis failed:\n" + "\n".join(f"- {x}" for x in report.get("issues", []))
    lines = ["Execution Plan:"]
    for row in report.get("explain") or []:
        detail = row.get("detail") or row.get("DETAIL") or str(row) if isinstance(row, dict) else str(row)
        lines.append(f"  {detail}")
    lines.extend(["", "Suggestions:", *[f"- {h}" for h in report.get("hints", [])]])
    return "\n".join(lines)


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
