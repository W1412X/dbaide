"""LLM-based semantic join inference between disclosed tables (no keyword heuristics)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.assets import AssetStore
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.agent.join_validation import type_alignment_score
from dbaide.models import ColumnInfo

logger = logging.getLogger("dbaide.join_inference")

DisclosedSchema = tuple[str, str, list[ColumnInfo]]
ProgressFn = Callable[[dict[str, Any]], None]

MIN_CONFIDENCE = 0.25
INFER_RETRIES = 2


class SemanticJoinInferencer:
    """Propose JOIN edges from schema semantics, types, and the user question."""

    def __init__(self, llm: LLMClient, store: AssetStore, instance: str) -> None:
        if isinstance(llm, NullLLMClient):
            raise ModelRequiredError("LLM is required for semantic join inference.")
        self.llm = llm
        self.store = store
        self.instance = instance

    def infer(
        self,
        question: str,
        disclosed: list[DisclosedSchema],
        *,
        declared: list[dict[str, Any]] | None = None,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> list[dict[str, Any]]:
        if len(disclosed) < 2:
            return []
        self._emit(
            progress,
            parent,
            f"Inferring joins across {len(disclosed)} table(s)",
            detail=question[:160] if question else "",
        )
        payload: dict[str, Any] | None = None
        last_error = ""
        user = self._build_user_prompt(question, disclosed, declared or [])
        for attempt in range(INFER_RETRIES):
            try:
                payload = self.llm.complete_json(
                    [
                        LLMMessage("system", _system_prompt()),
                        LLMMessage("user", user),
                    ],
                    schema_hint=(
                        'Return {"joins":[{"left_table":"t1","left_column":"c1",'
                        '"right_table":"t2","right_column":"c2","confidence":0.8,"reason":"..."}]}'
                    ),
                )
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("semantic_join_attempt_failed attempt=%d: %s", attempt + 1, exc)
        if not isinstance(payload, dict):
            if last_error:
                self._emit(progress, parent, f"Join inference failed: {last_error}", status="failed")
            return []

        raw_joins = payload.get("joins") or payload.get("relations") or []
        validated = self._validate_joins(raw_joins, disclosed, declared or [])
        self._emit(
            progress,
            parent,
            f"Inferred {len(validated)} semantic join(s)",
            detail="; ".join(
                f"{j['table']}.{j['column']}→{j['ref_table']}.{j['ref_column']}" for j in validated[:4]
            ),
            status="completed" if validated else "info",
        )
        return validated

    def _build_user_prompt(
        self,
        question: str,
        disclosed: list[DisclosedSchema],
        declared: list[dict[str, Any]],
    ) -> str:
        blocks = [f"User question:\n{question.strip() or '(not provided)'}\n"]
        blocks.append("Disclosed tables (use ONLY these names and columns):")
        for database, table, columns in disclosed:
            label = f"{database}.{table}" if database else table
            table_doc = self.store.table_doc(self.instance, database, table) if database else None
            table_summary = ""
            if table_doc:
                table_summary = str(
                    table_doc.get("description") or table_doc.get("semantic_summary") or table_doc.get("source_comment") or ""
                ).strip()
            blocks.append(f"\nTable: {label}")
            if table_summary:
                blocks.append(f"Table summary: {table_summary[:400]}")
            blocks.append("Columns:")
            for col in columns:
                blocks.append(self._format_column(database, table, col))
        if declared:
            blocks.append("\nAlready declared foreign keys (do NOT duplicate):")
            for fk in declared:
                blocks.append(
                    f"- {fk.get('table')}.{fk.get('column')} -> {fk.get('ref_table')}.{fk.get('ref_column')}"
                )
        blocks.append(
            "\nPropose additional JOIN edges needed to answer the question. "
            "Return empty joins if declared FKs already suffice or if unsure."
        )
        return "\n".join(blocks)

    def _format_column(self, database: str, table: str, column: ColumnInfo) -> str:
        doc = self._column_asset_doc(database, table, column.name)
        semantic = str((doc or {}).get("semantic_summary") or "").strip()
        comment = str(column.comment or (doc or {}).get("source_comment") or "").strip()
        parts = [
            f"- {column.name}: {column.data_type}",
            f"pk={column.primary_key}",
            f"indexed={column.indexed}",
        ]
        if comment:
            parts.append(f"comment={comment[:200]}")
        if semantic and semantic != comment:
            parts.append(f"semantic={semantic[:200]}")
        return ", ".join(parts)

    def _column_asset_doc(self, database: str, table: str, col_name: str) -> dict[str, Any] | None:
        if database:
            table_doc = self.store.table_doc(self.instance, database, table)
            if table_doc:
                for col in table_doc.get("columns") or []:
                    if str(col.get("name") or col.get("column") or "") == col_name:
                        return col
            for doc in self.store.column_docs(self.instance, database, table):
                if str(doc.get("name") or doc.get("column") or "") == col_name:
                    return doc
        return None

    def _validate_joins(
        self,
        raw_joins: list[Any],
        disclosed: list[DisclosedSchema],
        declared: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        col_index = _column_index(disclosed)
        col_types = _column_type_index(disclosed)
        table_names = {table for _, table, _ in disclosed}
        declared_keys = {
            _relation_key(
                str(fk.get("table") or ""),
                str(fk.get("column") or ""),
                str(fk.get("ref_table") or ""),
                str(fk.get("ref_column") or ""),
            )
            for fk in declared
        }
        seen: set[tuple[str, str, str, str]] = set()
        out: list[dict[str, Any]] = []

        for raw in raw_joins:
            if not isinstance(raw, dict):
                continue
            left_table = str(raw.get("left_table") or raw.get("table") or "").strip()
            left_col = str(raw.get("left_column") or raw.get("column") or "").strip()
            right_table = str(raw.get("right_table") or raw.get("ref_table") or "").strip()
            right_col = str(raw.get("right_column") or raw.get("ref_column") or "").strip()
            if not all([left_table, left_col, right_table, right_col]):
                continue
            if left_table not in table_names or right_table not in table_names:
                continue
            if left_table == right_table:
                continue
            if (left_table, left_col) not in col_index or (right_table, right_col) not in col_index:
                continue
            left_type = col_types.get((left_table, left_col), "")
            right_type = col_types.get((right_table, right_col), "")
            try:
                confidence = float(raw.get("confidence", 0.6))
            except (TypeError, ValueError):
                confidence = 0.6
            alignment = type_alignment_score(left_type, right_type)
            confidence = min(1.0, confidence * (0.55 + 0.45 * alignment))
            if confidence < MIN_CONFIDENCE:
                continue
            key = (left_table, left_col, right_table, right_col)
            rev_key = (right_table, right_col, left_table, left_col)
            if key in seen or rev_key in seen:
                continue
            rel_key = _relation_key(*key)
            if rel_key in declared_keys:
                continue
            seen.add(key)
            out.append(
                {
                    "table": left_table,
                    "column": left_col,
                    "ref_table": right_table,
                    "ref_column": right_col,
                    "source": "semantic",
                    "confidence": round(confidence, 3),
                    "type_alignment": round(alignment, 3),
                    "reason": str(raw.get("reason") or "").strip()[:280],
                }
            )
        return out

    def _emit(
        self,
        progress: ProgressFn | None,
        parent: str,
        title: str,
        *,
        detail: str = "",
        status: str = "info",
    ) -> None:
        if not progress:
            return
        from dbaide.agent.progress_events import subagent_event

        progress(subagent_event(agent="join_infer", title=title, parent=parent, detail=detail, status=status))


def _system_prompt() -> str:
    return (
        "You infer JOIN relationships between disclosed database tables.\n"
        "Use column semantics, comments, table summaries, types, and the user question.\n"
        "You are the primary judge — propose joins the business needs even when types differ "
        "(explain casts or conversions in reason).\n"
        "Do NOT use column-name pattern rules (e.g. never assume *_id implies a table).\n"
        "Return JSON only:\n"
        '{"joins":[{"left_table":"...","left_column":"...","right_table":"...","right_column":"...",'
        '"confidence":0.0,"reason":"..."}]}\n'
        "Rules:\n"
        "- Use exact table and column names from the prompt.\n"
        "- confidence 0.0-1.0 reflects your semantic certainty.\n"
        "- Do not duplicate already declared foreign keys.\n"
        "- Empty joins is valid when declared FKs suffice or evidence is weak."
    )


def _column_index(disclosed: list[DisclosedSchema]) -> set[tuple[str, str]]:
    index: set[tuple[str, str]] = set()
    for _, table, columns in disclosed:
        for col in columns:
            index.add((table, col.name))
    return index


def _column_type_index(disclosed: list[DisclosedSchema]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for _, table, columns in disclosed:
        for col in columns:
            index[(table, col.name)] = col.data_type
    return index


def _relation_key(table: str, column: str, ref_table: str, ref_column: str) -> tuple[str, str, str, str]:
    return (table, column, ref_table, ref_column)


def tables_fully_connected(relations: list[dict[str, Any]], table_names: set[str]) -> bool:
    """True when every disclosed table is in one connected component via known edges."""
    if len(table_names) <= 1:
        return True
    if not relations:
        return False
    parent = {name: name for name in table_names}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rel in relations:
        left = str(rel.get("table") or "")
        right = str(rel.get("ref_table") or "")
        if left in table_names and right in table_names:
            union(left, right)
    roots = {find(name) for name in table_names}
    return len(roots) <= 1


def merge_relation_lists(
    declared: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge FK and semantic joins; declared FK wins on duplicate edges."""
    out = list(declared)
    seen = {_relation_key(
        str(r.get("table") or ""),
        str(r.get("column") or ""),
        str(r.get("ref_table") or ""),
        str(r.get("ref_column") or ""),
    ) for r in declared}
    for rel in semantic:
        key = _relation_key(
            str(rel.get("table") or ""),
            str(rel.get("column") or ""),
            str(rel.get("ref_table") or ""),
            str(rel.get("ref_column") or ""),
        )
        rev = (key[2], key[3], key[0], key[1])
        if key in seen or rev in seen:
            continue
        seen.add(key)
        out.append(rel)
    return out
