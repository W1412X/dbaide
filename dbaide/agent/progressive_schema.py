"""LLM-driven progressive schema discovery: instance → database → table → column."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from dbaide.assets import AssetStore
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


@dataclass(slots=True)
class DiscoveryResult:
    question: str
    hits: list[SchemaHit] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class ModelRequiredError(RuntimeError):
    """Raised when LLM is required but not configured."""


class ProgressiveSchemaAgent:
    """Navigate offline assets level-by-level using LLM relevance judgments."""

    def __init__(self, llm: LLMClient, store: AssetStore, instance: str) -> None:
        if isinstance(llm, NullLLMClient):
            raise ModelRequiredError(
                "A configured LLM is required. Open Settings → Models and set provider, base URL, API key, and model ID."
            )
        self.llm = llm
        self.store = store
        self.instance = instance

    def discover(
        self,
        question: str,
        *,
        schema_tools: SchemaTools | None = None,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> DiscoveryResult:
        if self.store.has_instance(self.instance):
            return self._discover_from_assets(question, progress=progress, parent=parent)
        if schema_tools is not None:
            return self._discover_from_live(schema_tools, question, progress=progress, parent=parent)
        result = DiscoveryResult(question=question)
        result.trace.append("No offline assets — build assets first, or connect with live schema access.")
        return result

    def _discover_from_assets(
        self,
        question: str,
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> DiscoveryResult:
        result = DiscoveryResult(question=question)
        if not self.store.has_instance(self.instance):
            result.trace.append("No offline assets for this connection — build assets first.")
            return result

        databases = self.store.database_docs(self.instance)
        if not databases:
            result.trace.append("Asset index has no databases.")
            return result

        result.trace.append(f"Screening {len(databases)} database(s)…")
        self._emit_progress(
            progress,
            parent,
            "schema_link",
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
            "schema_link",
            f"Kept {len(db_indices)} database(s)",
            detail=", ".join(db_items[i]["name"] for i in db_indices[:6]),
        )

        table_hits: list[SchemaHit] = []
        column_hits: list[SchemaHit] = []

        def _scan_database(db_index: int) -> tuple[list[SchemaHit], list[SchemaHit], str]:
            db_name = db_items[db_index]["name"]
            tables = self.store.table_docs(self.instance, db_name)
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
                if len(local_tables) <= MAX_COLUMN_TABLES:
                    cols = self.store.column_docs(self.instance, db_name, table_name)
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
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_database, idx): idx for idx in db_indices}
            for future in as_completed(futures):
                idx = futures[future]
                db_name = db_items[idx]["name"]
                try:
                    tables, columns, note = future.result()
                    table_hits.extend(tables)
                    column_hits.extend(columns)
                    result.trace.append(note)
                    self._emit_progress(progress, parent, "schema_link", note,
                                        node_id=f"schema:{db_name}", status="completed")
                except Exception as exc:
                    logger.warning("database_scan_failed: %s", exc)
                    result.trace.append(f"scan error: {exc}")
                    self._emit_progress(progress, parent, "schema_link", f"scan error: {exc}",
                                        node_id=f"schema:{db_name}", status="failed")

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
            futures = {pool.submit(_scan_live_database, idx): idx for idx in db_indices}
            for future in as_completed(futures):
                idx = futures[future]
                db_name = db_items[idx]["name"]
                try:
                    tables, columns, note = future.result()
                    table_hits.extend(tables)
                    column_hits.extend(columns)
                    result.trace.append(note)
                    self._emit_progress(progress, parent, "schema_link", note,
                                        node_id=f"schema:{db_name}", status="completed")
                except Exception as exc:
                    logger.warning("live_database_scan_failed: %s", exc)
                    result.trace.append(f"scan error: {exc}")
                    self._emit_progress(progress, parent, "schema_link", f"scan error: {exc}",
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
        context = "\n".join(lines)
        text = self.llm.complete_text(
            [
                LLMMessage(
                    "system",
                    "You are a database schema assistant. Answer using ONLY the relevant schema below. "
                    "Be concise. Use the user's language. Format as markdown with bullet groups by database. "
                    "Do NOT list unrelated tables.",
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

        progress(subagent_event(
            agent=agent, title=title, parent=parent, detail=detail,
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
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            batch_no = start // BATCH_SIZE + 1
            self._emit_progress(
                progress,
                parent,
                "schema_link",
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
            reason = str(payload.get("reason") or "").strip()
            self._emit_progress(
                progress,
                parent,
                "schema_link",
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
        f"You filter {level} objects for relevance to the user's question.\n"
        "Return JSON only: {\"relevant_indices\": [<index values from the batch>], \"reason\": \"...\"}\n"
        "Rules:\n"
        "- Include ONLY objects clearly related to the question.\n"
        "- Prefer precision over recall; empty array is OK.\n"
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
