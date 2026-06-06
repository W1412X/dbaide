"""Schema evidence retrieval for the single-brain Ask agent.

This module intentionally does not decide the final schema. It gathers only
schema evidence the main loop can reason over: candidate tables, user notes,
columns, asset metadata, conflicts, and missing signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbaide.agent.memory import SchemaCandidate, SchemaEvidenceReport
from dbaide.agent.progress_events import child_node, subagent_event
from dbaide.agent.schema_context import sanitize_note
from dbaide.agent.toolkit.support import _remember_table_schema
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator


@dataclass(slots=True)
class SchemaContextReport:
    id: str
    request: str
    actions_taken: list[str] = field(default_factory=list)
    candidates: list[SchemaCandidate] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    source_summary: str = ""

    def to_memory_report(self) -> SchemaEvidenceReport:
        return SchemaEvidenceReport(
            id=self.id,
            request=self.request,
            actions_taken=list(self.actions_taken),
            candidates=list(self.candidates),
            joins=[],
            conflicts=list(self.conflicts),
            missing=list(self.missing),
            source_summary=self.source_summary,
        )

    def to_tool_data(self) -> dict[str, Any]:
        return {
            "report_id": self.id,
            "request": self.request,
            "actions_taken": list(self.actions_taken),
            "candidates": [_candidate_to_dict(c) for c in self.candidates],
            "conflicts": list(self.conflicts),
            "missing": list(self.missing),
            "source_summary": self.source_summary,
            "instruction": (
                "This is evidence, not a final schema decision. The main LLM must decide "
                "whether to ask the user, inspect more tables/columns, call retrieve_join_context, "
                "generate SQL, or run exploratory SQL."
            ),
        }


class SchemaEvidenceRetriever:
    PARENT = "retrieve_schema_context"

    def __init__(self, orchestrator: "AskOrchestrator") -> None:
        self.orch = orchestrator

    def retrieve(
        self,
        request: str,
        *,
        database: str = "",
        focus_terms: list[str] | None = None,
        scope: dict[str, Any] | None = None,
        need: str = "",
        limit: int = 8,
    ) -> SchemaContextReport:
        base = self.orch.run_state.trace_node or self.PARENT
        report_id = f"schema:{len(self.orch.run_state.memory.schema_reports) + 1}"
        actions: list[str] = []
        candidates: list[SchemaCandidate] = []
        missing: list[str] = []

        search_text = _request_text(request, focus_terms or [], need)
        discover_node = child_node(base, "candidate recall")
        self.orch.progress(subagent_event(
            agent="schema_link",
            parent_id=base,
            node_id=discover_node,
            title="Recall schema candidates",
            status="running",
            detail=search_text[:160],
        ))
        try:
            discovery = self.orch._discover(search_text, parent=discover_node, column_detail=False)
            actions.append(f"progressive discovery for: {search_text}")
        except Exception as exc:
            discovery = None
            missing.append(f"progressive discovery failed: {exc}")

        targets = _targets_from_scope(scope, database)
        if discovery is not None:
            for hit in discovery.hits:
                if hit.kind == "table" and hit.table:
                    db = hit.database or database or ""
                    key = (db, hit.table)
                    if key not in targets:
                        targets.append(key)
                if len(targets) >= max(1, limit):
                    break

        self.orch.progress(subagent_event(
            agent="schema_link",
            parent_id=base,
            node_id=discover_node,
            title="Recall schema candidates",
            status="completed",
            detail=f"{len(targets)} candidate target(s)",
        ))

        notes = self._notes_for_targets(targets)
        for db, table in targets[:max(1, limit)]:
            candidate = self._candidate(db, table, notes)
            candidates.append(candidate)
            if candidate.columns:
                cols = [_column_from_payload(c) for c in candidate.columns]
                _remember_table_schema(self.orch, table, db, cols)
            if candidate.status != "active":
                actions.append(f"kept excluded/deprecated evidence for {db}.{table}")
            else:
                actions.append(f"loaded table evidence for {db}.{table}")

        conflicts = _detect_conflicts(candidates)
        source_summary = (
            f"{len(candidates)} candidate table(s), "
            f"{sum(1 for c in candidates if c.status == 'active')} active, "
            f"{len(conflicts)} conflict(s)."
        )
        report = SchemaContextReport(
            id=report_id,
            request=request,
            actions_taken=actions,
            candidates=candidates,
            conflicts=conflicts,
            missing=missing,
            source_summary=source_summary,
        )
        self.orch.run_state.memory.add_schema_report(report.to_memory_report())
        return report

    def _candidate(
        self,
        database: str,
        table: str,
        notes: dict[tuple[str, str], dict[str, Any]],
    ) -> SchemaCandidate:
        db_l = database.strip().lower()
        tbl_l = table.strip().lower()
        note_entry = notes.get((db_l, tbl_l)) or {}
        table_note = sanitize_note(str(note_entry.get("table") or ""))
        column_notes = note_entry.get("columns") or {}
        status = "active"
        exclusion_reason = ""
        if _is_deprecated_note(table_note):
            status = "deprecated"
            exclusion_reason = table_note

        columns: list[dict[str, Any]] = []
        row_count = None
        indexes: list[Any] = []
        foreign_keys: list[Any] = []
        summary = ""
        tdoc = self.orch.asset_store.table_doc(
            self.orch.instance,
            database,
            table,
            fingerprint=getattr(self.orch, "connection_fingerprint", ""),
        )
        if tdoc:
            summary = str(tdoc.get("summary") or tdoc.get("comment") or "")[:240]
            row_count = tdoc.get("row_count")
            indexes = list(tdoc.get("indexes") or [])
            foreign_keys = list(tdoc.get("foreign_keys") or [])
            for col in (tdoc.get("columns") or [])[:80]:
                name = str(col.get("name") or "")
                note = sanitize_note(str(column_notes.get(name.lower()) or ""))
                columns.append({
                    "name": name,
                    "data_type": str(col.get("data_type") or col.get("type") or ""),
                    "primary_key": bool(col.get("primary_key")),
                    "indexed": bool(col.get("indexed")),
                    "comment": str(col.get("comment") or "")[:160],
                    "note": note,
                })
        if not columns:
            try:
                live_cols = self.orch.schema.describe_table(table, database=database)
            except Exception as exc:
                return SchemaCandidate(
                    database=database,
                    table=table,
                    columns=[],
                    summary=summary,
                    notes={"table": table_note, "columns": column_notes},
                    status="missing",
                    exclusion_reason=f"describe_table failed: {exc}",
                )
            for col in live_cols[:80]:
                note = sanitize_note(str(column_notes.get(col.name.lower()) or ""))
                columns.append({
                    "name": col.name,
                    "data_type": col.data_type,
                    "primary_key": col.primary_key,
                    "indexed": col.indexed,
                    "comment": (col.comment or "")[:160],
                    "note": note,
                })
        if not indexes:
            try:
                indexes = [idx.to_dict() if hasattr(idx, "to_dict") else idx for idx in self.orch.adapter.indexes(table, database=database)]
            except Exception:
                indexes = []
        if not foreign_keys:
            try:
                foreign_keys = [
                    {
                        "table": fk.table,
                        "column": fk.column,
                        "ref_table": fk.ref_table,
                        "ref_column": fk.ref_column,
                        "source": "foreign_key",
                    }
                    for fk in self.orch.schema.foreign_keys(table, database=database)
                ]
            except Exception:
                foreign_keys = []
        return SchemaCandidate(
            database=database,
            table=table,
            columns=columns,
            summary=summary,
            notes={"table": table_note, "columns": column_notes},
            status=status,
            exclusion_reason=exclusion_reason,
            row_count=row_count,
            indexes=indexes,
            foreign_keys=foreign_keys,
        )

    def _notes_for_targets(self, targets: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        store = getattr(self.orch, "annotations", None)
        if store is None or not targets:
            return {}
        try:
            view = store.annotations_for_tables(self.orch.instance, targets)
        except Exception:
            return {}
        tnotes = view.get("tables") or {}
        dbnotes = view.get("databases") or {}
        cnotes = view.get("columns") or {}
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for db, tbl in targets:
            db_l, tbl_l = db.strip().lower(), tbl.strip().lower()
            entry = {
                "table": tnotes.get((db_l, tbl_l)) or dbnotes.get(db_l) or dbnotes.get("") or "",
                "columns": cnotes.get((db_l, tbl_l)) or {},
            }
            out[(db_l, tbl_l)] = entry
        return out


def _request_text(request: str, focus_terms: list[str], need: str) -> str:
    parts = [str(request or "").strip()]
    if focus_terms:
        parts.append("Focus terms: " + ", ".join(str(x) for x in focus_terms if str(x).strip()))
    if need:
        parts.append("Need: " + str(need).strip())
    return "\n".join(p for p in parts if p)


def _targets_from_scope(scope: dict[str, Any] | None, database: str) -> list[tuple[str, str]]:
    if not isinstance(scope, dict):
        return []
    out: list[tuple[str, str]] = []
    for raw in scope.get("tables") or []:
        if not isinstance(raw, dict):
            continue
        table = str(raw.get("table") or "").strip()
        if not table:
            continue
        db = str(raw.get("database") or database or "").strip()
        out.append((db, table))
    return out


def _candidate_to_dict(c: SchemaCandidate) -> dict[str, Any]:
    return {
        "database": c.database,
        "table": c.table,
        "columns": c.columns,
        "summary": c.summary,
        "notes": c.notes,
        "status": c.status,
        "exclusion_reason": c.exclusion_reason,
        "row_count": c.row_count,
        "indexes": c.indexes,
        "foreign_keys": c.foreign_keys,
    }


def _column_from_payload(data: dict[str, Any]) -> ColumnInfo:
    return ColumnInfo(
        name=str(data.get("name") or ""),
        data_type=str(data.get("data_type") or ""),
        comment=str(data.get("comment") or ""),
        primary_key=bool(data.get("primary_key")),
        indexed=bool(data.get("indexed")),
        note=str(data.get("note") or ""),
    )


def _detect_conflicts(candidates: list[SchemaCandidate]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    active = [c for c in candidates if c.status == "active"]
    by_metric: dict[str, list[str]] = {}
    for c in active:
        label = f"{c.database}.{c.table}" if c.database else c.table
        for col in c.columns:
            name = str(col.get("name") or "").lower()
            if _is_metric_column(name):
                by_metric.setdefault(name, []).append(label)
    for name, tables in by_metric.items():
        if len(tables) >= 2:
            conflicts.append({
                "type": "metric_grain_choice",
                "column": name,
                "tables": tables,
                "reason": (
                    f"Multiple active candidate tables expose metric-like column `{name}`; "
                    "table/grain choice may change the answer."
                ),
            })
    deprecated = [c for c in candidates if c.status != "active"]
    for c in deprecated:
        conflicts.append({
            "type": c.status,
            "table": f"{c.database}.{c.table}" if c.database else c.table,
            "reason": c.exclusion_reason or c.status,
        })
    return conflicts


def _is_metric_column(name: str) -> bool:
    if not name:
        return False
    if name in {"id", "dt", "date", "created_at", "updated_at"} or name.endswith("_id"):
        return False
    return any(token in name for token in (
        "amount", "quantity", "qty", "count", "cnt", "num", "total", "sum",
        "price", "cost", "sales", "orders", "pieces", "refund", "delivered",
        "units", "revenue", "gmv",
    ))


def _is_deprecated_note(note: str) -> bool:
    text = str(note or "").lower()
    return any(token in text for token in ("弃用", "停用", "deprecated", "wrong", "do not use"))
