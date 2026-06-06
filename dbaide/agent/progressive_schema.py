"""LLM-driven progressive schema discovery: instance → database → table → column."""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from dbaide.assets import AssetStore
from dbaide.i18n import answer_language_directive
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

if TYPE_CHECKING:
    from dbaide.tools.schema import SchemaTools

ProgressFn = Callable[[dict[str, Any]], None]

logger = logging.getLogger("dbaide.progressive_schema")

BATCH_SIZE = 18
FILTER_RETRIES = 2
MAX_COLUMN_TABLES = 3


@dataclass(slots=True)
class SchemaHit:
    kind: str
    path: str
    name: str
    database: str = ""
    table: str = ""
    summary: str = ""
    reason: str = ""
    note: str = ""  # authoritative user note for this object (db/table/column)


@dataclass(slots=True)
class DiscoveryResult:
    question: str
    hits: list[SchemaHit] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class ModelRequiredError(RuntimeError):
    """Raised when LLM is required but not configured."""


class ProgressiveSchemaAgent:
    """Navigate offline assets level-by-level using LLM relevance judgments."""

    def __init__(self, llm: LLMClient, store: AssetStore | None, instance: str, *, fingerprint: str = "") -> None:
        if isinstance(llm, NullLLMClient):
            raise ModelRequiredError(
                "A configured LLM is required. Open Settings → Models and set provider, base URL, API key, and model ID."
            )
        self.llm = llm
        self.store = store or AssetStore()
        self.instance = instance
        self.fingerprint = fingerprint

    def discover(
        self,
        question: str,
        *,
        schema_tools: SchemaTools | None = None,
        progress: ProgressFn | None = None,
        parent: str = "",
        column_detail: bool = True,
        scope: dict[str, Any] | None = None,
    ) -> DiscoveryResult:
        # Assets first: navigate the offline docs by relevance (instance → database →
        # table). Only fall back to the live catalog when there are no assets — never
        # blind-list the whole database. ``column_detail=False`` returns just the
        # relevant tables (the "big direction") and skips the per-column LLM pass,
        # which the schema linker does in a single shot instead.
        scope = scope or {}
        scope_tables = scope.get("tables") or []
        scope_dbs = scope.get("databases") or []
        if self.store.has_instance(self.instance, fingerprint=self.fingerprint):
            # User pinned a db/table → prioritise that scope; only broaden to the full
            # progressive crawl if the scope can't yield anything usable.
            if scope_tables or scope_dbs:
                scoped = self._discover_scoped(
                    question, scope_tables, scope_dbs,
                    progress=progress, parent=parent, column_detail=column_detail,
                )
                if scoped.hits:
                    scoped.trace.insert(0, "Prioritised user-provided schema scope.")
                    return scoped
                result = DiscoveryResult(question=question)
                result.trace.append("User scope yielded no assets — falling back to full discovery.")
                self._emit_progress(progress, parent, "", "Scope empty — broadening to full discovery")
            return self._discover_from_assets(question, progress=progress, parent=parent, column_detail=column_detail)
        if schema_tools is not None:
            return self._discover_from_live(schema_tools, question, progress=progress, parent=parent)
        result = DiscoveryResult(question=question)
        result.trace.append("No offline assets — build assets first, or connect with live schema access.")
        return result

    def _find_table_database(self, table: str) -> str:
        """Locate which database a bare table name lives in (first match)."""
        if not table:
            return ""
        for db_doc in self.store.database_docs(self.instance, fingerprint=self.fingerprint):
            db_name = str(db_doc.get("name") or "")
            for td in self.store.table_docs(self.instance, db_name, fingerprint=self.fingerprint):
                if str(td.get("name") or td.get("table") or "") == table:
                    return db_name
        return ""

    def _discover_scoped(
        self,
        question: str,
        scope_tables: list[dict[str, Any]],
        scope_dbs: list[str],
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
        column_detail: bool = True,
    ) -> DiscoveryResult:
        """Discover within the user-pinned scope: the provided tables (seeded
        directly with their columns) plus a screening of the relevant databases
        (provided databases + the databases of provided tables) for related/join
        tables. Returns empty hits if the scope matches no assets (caller broadens)."""
        target_dbs = {str(d) for d in scope_dbs if str(d).strip()}
        norm_tables: list[tuple[str, str]] = []
        for t in scope_tables:
            tbl = str(t.get("table") or t.get("name") or "").strip()
            if not tbl:
                continue
            db = str(t.get("database") or "").strip() or self._find_table_database(tbl)
            if db:
                target_dbs.add(db)
            norm_tables.append((db, tbl))

        self._emit_progress(
            progress, parent, "", "Using user-provided schema scope",
            detail=", ".join(sorted(target_dbs)) or "tables",
        )
        # Screen the in-scope databases for related tables (no LLM db filter — the
        # user already chose the databases).
        result = self._discover_from_assets(
            question, progress=progress, parent=parent, column_detail=column_detail,
            restrict_databases=target_dbs or None,
        )
        # Ensure every explicitly provided table is present and prioritised, with its
        # columns seeded (the user picked it, so don't let screening drop it).
        have = {(h.database, h.table) for h in result.hits if h.kind == "table"}
        for db, tbl in norm_tables:
            if not db or (db, tbl) in have:
                continue
            result.hits.insert(0, SchemaHit(
                kind="table", path=f"{self.instance}.{db}.{tbl}", name=tbl,
                database=db, table=tbl, reason="user-provided",
            ))
            for cdoc in self.store.column_docs(self.instance, db, tbl, fingerprint=self.fingerprint)[:48]:
                cn = str(cdoc.get("name") or cdoc.get("column") or "")
                if cn:
                    result.hits.append(SchemaHit(
                        kind="column", path=f"{self.instance}.{db}.{tbl}.{cn}", name=cn,
                        database=db, table=tbl,
                        summary=str(cdoc.get("semantic_summary") or cdoc.get("source_comment") or ""),
                    ))
        return result

    def _discover_from_assets(
        self,
        question: str,
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
        column_detail: bool = True,
        restrict_databases: set[str] | None = None,
    ) -> DiscoveryResult:
        result = DiscoveryResult(question=question)
        if not self.store.has_instance(self.instance, fingerprint=self.fingerprint):
            result.trace.append("No offline assets for this connection — build assets first.")
            return result

        databases = self.store.database_docs(self.instance, fingerprint=self.fingerprint)
        if not databases:
            result.trace.append("Asset index has no databases.")
            return result

        result.trace.append(f"Screening {len(databases)} database(s)…")
        self._emit_progress(
            progress,
            parent,
            "",
            f"Screening {len(databases)} database(s)",
            detail=f"connection={self.instance}",
        )
        db_items = [
            {
                "index": i,
                "name": str(doc.get("name") or ""),
                "summary": _brief(str(doc.get("description") or doc.get("semantic_summary") or "")),
            }
            for i, doc in enumerate(databases)
            if doc.get("name")
        ]
        if restrict_databases:
            # Scope pinned by the user → use exactly those databases, no LLM filter.
            db_indices = [it["index"] for it in db_items if it["name"] in restrict_databases]
        elif len(db_items) <= 1:
            # One database → nothing to filter; skip the LLM round-trip entirely.
            db_indices = [it["index"] for it in db_items]
        else:
            db_indices = self._filter_indices(
                question,
                level="database",
                items=db_items,
                context=f"connection={self.instance}",
                progress=progress,
                parent=parent,
            )
        result.trace.append(f"LLM kept {len(db_indices)} database(s).")
        self._emit_progress(
            progress,
            parent,
            "",
            f"Kept {len(db_indices)} database(s)",
            detail=", ".join(db_items[i]["name"] for i in db_indices[:6]),
        )

        table_hits: list[SchemaHit] = []
        column_hits: list[SchemaHit] = []

        def _scan_database(db_index: int) -> tuple[list[SchemaHit], list[SchemaHit], str]:
            db_name = db_items[db_index]["name"]
            tables = self.store.table_docs(self.instance, db_name, fingerprint=self.fingerprint)
            if not tables:
                return [], [], f"{db_name}: no tables"
            table_items = [
                {
                    "index": i,
                    "name": str(doc.get("name") or doc.get("table") or ""),
                    "summary": _brief(str(doc.get("semantic_summary") or doc.get("description") or "")),
                }
                for i, doc in enumerate(tables)
                if doc.get("name") or doc.get("table")
            ]
            kept = self._filter_indices(
                question,
                level="table",
                items=table_items,
                context=f"database={db_name}",
                progress=progress,
                parent=parent,
            )
            local_tables: list[SchemaHit] = []
            local_columns: list[SchemaHit] = []
            for ti in kept:
                doc = tables[ti]
                table_name = str(doc.get("name") or doc.get("table") or "")
                summary = str(doc.get("semantic_summary") or doc.get("description") or "")
                local_tables.append(
                    SchemaHit(
                        kind="table",
                        path=f"{self.instance}.{db_name}.{table_name}",
                        name=table_name,
                        database=db_name,
                        table=table_name,
                        summary=summary,
                    )
                )
                if column_detail and len(local_tables) <= MAX_COLUMN_TABLES:
                    cols = self.store.column_docs(self.instance, db_name, table_name, fingerprint=self.fingerprint)
                    if not cols:
                        continue
                    col_items = [
                        {
                            "index": i,
                            "name": str(c.get("name") or c.get("column") or ""),
                            "summary": _brief(str(c.get("semantic_summary") or c.get("source_comment") or "")),
                            "data_type": str(c.get("data_type") or ""),
                        }
                        for i, c in enumerate(cols)
                        if c.get("name") or c.get("column")
                    ]
                    col_kept = self._filter_indices(
                        question,
                        level="column",
                        items=col_items,
                        context=f"table={db_name}.{table_name}",
                        progress=progress,
                        parent=parent,
                    )
                    for ci in col_kept:
                        cdoc = cols[ci]
                        col_name = str(cdoc.get("name") or cdoc.get("column") or "")
                        local_columns.append(
                            SchemaHit(
                                kind="column",
                                path=f"{self.instance}.{db_name}.{table_name}.{col_name}",
                                name=col_name,
                                database=db_name,
                                table=table_name,
                                summary=str(cdoc.get("semantic_summary") or cdoc.get("source_comment") or ""),
                            )
                        )
            return local_tables, local_columns, f"{db_name}: kept {len(local_tables)} table(s)"

        workers = min(6, max(1, len(db_indices)))
        # Carry the active context (esp. the LLM-trace stage label) into workers —
        # contextvars don't cross ThreadPoolExecutor boundaries on their own. Each
        # worker gets its OWN context copy (a Context can't run concurrently).
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(contextvars.copy_context().run, _scan_database, idx): idx
                for idx in db_indices
            }
            for future in as_completed(futures):
                idx = futures[future]
                db_name = db_items[idx]["name"]
                try:
                    tables, columns, note = future.result()
                    table_hits.extend(tables)
                    column_hits.extend(columns)
                    result.trace.append(note)
                    self._emit_progress(progress, parent, "", note,
                                        node_id=f"{parent}/db:{db_name}", status="completed")
                except Exception as exc:
                    logger.warning("database_scan_failed: %s", exc)
                    result.trace.append(f"scan error: {exc}")
                    self._emit_progress(progress, parent, "", f"scan error: {exc}",
                                        node_id=f"{parent}/db:{db_name}", status="failed")

        for db_index in db_indices:
            db_name = db_items[db_index]["name"]
            doc = databases[db_index]
            if any(h.database == db_name for h in table_hits):
                continue
            result.hits.append(
                SchemaHit(
                    kind="database",
                    path=f"{self.instance}.{db_name}",
                    name=db_name,
                    database=db_name,
                    summary=str(doc.get("description") or doc.get("semantic_summary") or ""),
                )
            )

        result.hits.extend(table_hits)
        result.hits.extend(column_hits)
        result.hits.sort(key=lambda h: (0 if h.kind == "table" else 1 if h.kind == "column" else 2, h.path))
        return result

    def _discover_from_live(
        self,
        schema_tools: SchemaTools,
        question: str,
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> DiscoveryResult:
        """LLM-filter live adapter metadata when offline assets are unavailable."""
        result = DiscoveryResult(question=question)
        result.trace.append("Live schema discovery (no offline assets)…")

        databases = schema_tools.list_databases()
        if not databases:
            result.trace.append("No databases found on connection.")
            return result

        db_items = [{"index": i, "name": db, "summary": ""} for i, db in enumerate(databases)]
        if len(db_items) <= 1:
            db_indices = [it["index"] for it in db_items]   # one db → no filter needed
        else:
            db_indices = self._filter_indices(
                question, level="database", items=db_items, context=f"connection={self.instance}",
                progress=progress, parent=parent,
            )
        result.trace.append(f"LLM kept {len(db_indices)} database(s) from live catalog.")

        table_hits: list[SchemaHit] = []
        column_hits: list[SchemaHit] = []

        def _scan_live_database(db_index: int) -> tuple[list[SchemaHit], list[SchemaHit], str]:
            db_name = db_items[db_index]["name"]
            tables = schema_tools.list_tables(database=db_name)
            if not tables:
                return [], [], f"{db_name}: no tables"
            table_items = [
                {
                    "index": i,
                    "name": t.name,
                    "summary": _brief(str(t.comment or "")),
                }
                for i, t in enumerate(tables)
            ]
            kept = self._filter_indices(
                question, level="table", items=table_items, context=f"database={db_name}",
                progress=progress, parent=parent,
            )
            local_tables: list[SchemaHit] = []
            local_columns: list[SchemaHit] = []
            for ti in kept:
                table_name = table_items[ti]["name"]
                summary = table_items[ti]["summary"]
                local_tables.append(
                    SchemaHit(
                        kind="table",
                        path=f"{self.instance}.{db_name}.{table_name}",
                        name=table_name,
                        database=db_name,
                        table=table_name,
                        summary=summary,
                    )
                )
            if len(local_tables) <= MAX_COLUMN_TABLES:
                for hit in local_tables:
                    cols = schema_tools.describe_table(hit.table, database=db_name)
                    col_items = [
                        {
                            "index": i,
                            "name": c.name,
                            "summary": _brief(str(c.comment or "")),
                            "data_type": str(c.data_type or ""),
                        }
                        for i, c in enumerate(cols)
                    ]
                    col_kept = self._filter_indices(
                        question,
                        level="column",
                        items=col_items,
                        context=f"table={db_name}.{hit.table}",
                        progress=progress,
                        parent=parent,
                    )
                    for ci in col_kept:
                        cdoc = cols[ci]
                        local_columns.append(
                            SchemaHit(
                                kind="column",
                                path=f"{self.instance}.{db_name}.{hit.table}.{cdoc.name}",
                                name=cdoc.name,
                                database=db_name,
                                table=hit.table,
                                summary=str(cdoc.comment or ""),
                            )
                        )
            return local_tables, local_columns, f"{db_name}: kept {len(local_tables)} table(s)"

        # Live catalog reads (list_tables/describe_table) hit the database directly,
        # so keep discovery single-threaded to avoid spraying concurrent connections.
        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = {
                pool.submit(contextvars.copy_context().run, _scan_live_database, idx): idx
                for idx in db_indices
            }
            for future in as_completed(futures):
                idx = futures[future]
                db_name = db_items[idx]["name"]
                try:
                    tables, columns, note = future.result()
                    table_hits.extend(tables)
                    column_hits.extend(columns)
                    result.trace.append(note)
                    self._emit_progress(progress, parent, "", note,
                                        node_id=f"schema:{db_name}", status="completed")
                except Exception as exc:
                    logger.warning("live_database_scan_failed: %s", exc)
                    result.trace.append(f"scan error: {exc}")
                    self._emit_progress(progress, parent, "", f"scan error: {exc}",
                                        node_id=f"schema:{db_name}", status="failed")

        result.hits.extend(table_hits)
        result.hits.extend(column_hits)
        result.hits.sort(key=lambda h: (0 if h.kind == "table" else 1 if h.kind == "column" else 2, h.path))
        return result

    def synthesize_answer(
        self,
        question: str,
        discovery: DiscoveryResult,
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
        object_notes: list[dict[str, str]] | None = None,
    ) -> str:
        if not discovery.hits:
            return (
                "没有在离线资产中找到与问题明显相关的 schema。\n\n"
                "建议：\n"
                "- 确认已 Build Assets\n"
                "- 换更具体的业务词（如「产线」「production line」「line_id」）\n"
                "- 或直接指定库/表名"
            )
        self._emit_progress(
            progress,
            parent,
            "schema_synth",
            f"Synthesizing answer from {len(discovery.hits)} hit(s)",
        )
        lines = ["Relevant schema (progressive LLM screening):", ""]
        for hit in discovery.hits[:24]:
            prefix = f"**{hit.path}**"
            body = hit.summary or hit.reason or ""
            lines.append(f"- {prefix}")
            if body:
                lines.append(f"  {body[:280]}")
            # The user note travels with its object — show it right under the hit.
            note = str(getattr(hit, "note", "") or "").strip()
            if note:
                lines.append(f"  📝 USER NOTE (authoritative): {note}")
        notes = [n for n in (object_notes or []) if str(n.get("note") or "").strip()]
        if notes:
            lines += ["", "User notes (AUTHORITATIVE — override the summaries above):"]
            for n in notes:
                lines.append(f"- {n.get('scope')} {n.get('label')}: {str(n.get('note')).strip()}")
        context = "\n".join(lines)
        text = self.llm.complete_text(
            [
                LLMMessage(
                    "system",
                    "You are a database schema assistant. Answer using ONLY the relevant schema below. "
                    "Be concise. Format as markdown with bullet groups by database. "
                    "Do NOT list unrelated tables. "
                    "User notes are AUTHORITATIVE and override the summaries: if a note says a table "
                    "is deprecated/wrong or names a replacement, recommend the replacement and do NOT "
                    "point the user at the deprecated table. "
                    + answer_language_directive(),
                ),
                LLMMessage("user", f"Question:\n{question}\n\nSchema:\n{context}"),
            ]
        )
        self._emit_progress(progress, parent, "schema_synth", "Answer synthesized", status="completed")
        return text.strip()

    def _emit_progress(
        self,
        progress: ProgressFn | None,
        parent: str,
        agent: str,
        title: str,
        *,
        detail: str = "",
        status: str = "info",
        node_id: str = "",
    ) -> None:
        if not progress:
            return
        from dbaide.agent.progress_events import subagent_event

        # `parent` is the caller's trace node id — nest under it explicitly so the
        # discovery's internal work shows as children of the discovery activity.
        # An empty node_id lets the model derive a stable child id under parent_id.
        progress(subagent_event(
            agent=agent, title=title, parent_id=parent, detail=detail,
            status=status, node_id=node_id,
        ))

    def _filter_indices(
        self,
        question: str,
        *,
        level: str,
        items: list[dict],
        context: str,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> list[int]:
        if not items:
            return []
        kept: list[int] = []
        last_error: Exception | None = None
        multi_batch = len(items) > BATCH_SIZE  # only narrate batches when there are several
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            batch_no = start // BATCH_SIZE + 1
            if multi_batch:
                self._emit_progress(
                    progress, parent, "",
                    f"LLM filter {level} · batch {batch_no}",
                    detail=f"{len(batch)} object(s) · {context}",
                )
            payload: dict | None = None
            for attempt in range(FILTER_RETRIES):
                try:
                    payload = self.llm.complete_json(
                        [
                            LLMMessage("system", _filter_system(level)),
                            LLMMessage(
                                "user",
                                f"Question: {question}\nContext: {context}\n\nObjects:\n{_format_batch(batch)}",
                            ),
                        ],
                        schema_hint='Return {"relevant_indices":[0,1],"reason":"..."}',
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning("filter_attempt_failed level=%s attempt=%d: %s", level, attempt + 1, exc)
            if payload is None:
                if last_error:
                    raise last_error
                continue
            indices = payload.get("relevant_indices") or payload.get("indices") or []
            for raw in indices:
                try:
                    idx = int(raw)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(batch):
                    global_idx = int(batch[idx]["index"])
                    kept.append(global_idx)
            if multi_batch:
                reason = str(payload.get("reason") or "").strip()
                self._emit_progress(
                    progress, parent, "",
                    f"Batch {batch_no}: kept {len(indices)}",
                    detail=reason[:160] if reason else context,
                )
        return sorted(set(kept))

    def top_table(self, discovery: DiscoveryResult) -> tuple[str, str]:
        for hit in discovery.hits:
            if hit.kind == "table" and hit.table:
                return hit.table, hit.database
        return "", ""


def _filter_system(level: str) -> str:
    return (
        f"You shortlist {level} objects that might be relevant to the user's question.\n"
        "Return JSON only: {\"relevant_indices\": [<index values from the batch>], \"reason\": \"...\"}\n"
        "Rules:\n"
        "- This is a RECALL step in a funnel — a LATER step picks the final minimal set, "
        "so missing a relevant object here is far worse than including an extra one.\n"
        "- INCLUDE every object that could plausibly be relevant: directly, or as a "
        "join / lookup / reference / parent-child table, or by an ambiguous name. When "
        "in doubt, INCLUDE it.\n"
        "- Exclude only objects that are clearly unrelated to the question.\n"
        "- Use the 'index' field from each object; do not invent indices."
    )


def _format_batch(items: list[dict]) -> str:
    rows = []
    for item in items:
        row = f"[{item['index']}] {item['name']}"
        if item.get("data_type"):
            row += f" ({item['data_type']})"
        if item.get("summary"):
            row += f" — {item['summary'][:160]}"
        rows.append(row)
    return "\n".join(rows)


def _brief(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def format_discovery_markdown(discovery: DiscoveryResult) -> str:
    if not discovery.hits:
        return "No relevant schema found."
    lines: list[str] = []
    for hit in discovery.hits:
        summary = f" — {hit.summary[:200]}" if hit.summary else ""
        lines.append(f"- `{hit.path}`{summary}")
    return "\n".join(lines)
