"""Minimal agent memory for the conversation-stream architecture.

The agent loop now maintains a growing ``messages: list[LLMMessage]`` that the
model sees directly — no more compressed prompt_block() rendering.  This module
keeps only the *structured side-state* that the conversation stream alone cannot
carry: SQL artifacts (chart-tool lookup), confirmed facts (cross-run criteria),
excluded paths (cross-run avoidance), and verified facts (cross-run knowledge).

Schema evidence reports and join reports are kept as lightweight ID-tracking
collections so ``next_prefixed_id`` can avoid collisions; their *content* lives
in the conversation stream as tool-result messages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


MAX_SQL_ARTIFACTS = 128
MAX_EXCLUDED = 256
MAX_SCHEMA_REPORTS = 64
MAX_JOIN_REPORTS = 64


@dataclass(slots=True)
class ExcludedPath:
    target: str
    reason: str
    evidence_ref: str = ""
    source_priority: str = "evidence"


@dataclass(slots=True)
class SchemaCandidate:
    database: str
    table: str
    columns: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    notes: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    exclusion_reason: str = ""
    row_count: int | None = None
    indexes: list[Any] = field(default_factory=list)
    foreign_keys: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class SchemaEvidenceReport:
    id: str
    request: str
    actions_taken: list[str] = field(default_factory=list)
    candidates: list[SchemaCandidate] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    source_summary: str = ""


@dataclass(slots=True)
class JoinEvidenceReport:
    id: str
    request: str
    tables: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    source_summary: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SQLArtifact:
    id: str
    purpose: str
    sql: str
    database: str = ""
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    rows_preview: list[dict[str, Any]] = field(default_factory=list)
    result_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass(slots=True)
class AgentMemory:
    goal: str = ""
    intent: str = ""
    constraints: list[str] = field(default_factory=list)

    # Cross-run knowledge transfer
    confirmed_facts: list[str] = field(default_factory=list)
    verified_facts: list[str] = field(default_factory=list)
    excluded_paths: list[ExcludedPath] = field(default_factory=list)

    # Structured artifacts (chart-tool lookup needs sql_artifacts)
    sql_artifacts: list[SQLArtifact] = field(default_factory=list)

    # ID-tracking collections (content lives in conversation stream)
    schema_reports: list[SchemaEvidenceReport] = field(default_factory=list)
    join_reports: list[JoinEvidenceReport] = field(default_factory=list)

    def reset_goal(self, question: str, *, database: str = "", execute_allowed: bool = True) -> None:
        self.goal = str(question or "").strip()
        self.intent = "Answer one independent database question."
        self.constraints = [
            f"Database scope: {database or '(any)'}",
            f"SQL execution: {'allowed' if execute_allowed else 'disabled'}",
        ]

    def add_sql_artifact(self, artifact: SQLArtifact) -> None:
        self.sql_artifacts.append(artifact)
        self.sql_artifacts = self.sql_artifacts[-MAX_SQL_ARTIFACTS:]

    def add_exclusion(self, target: str, reason: str, *, evidence_ref: str = "",
                      source_priority: str = "evidence") -> None:
        target = _trim(target, 180)
        reason = _trim(reason, 400)
        if not target or not reason:
            return
        key = (target.lower(), reason.lower())
        if any((e.target.lower(), e.reason.lower()) == key for e in self.excluded_paths):
            return
        self.excluded_paths.append(ExcludedPath(target, reason, evidence_ref, source_priority))
        self.excluded_paths = self.excluded_paths[-MAX_EXCLUDED:]

    def mark_verified(self, text: str) -> None:
        text = _trim(text, 500)
        if text and text not in self.verified_facts:
            self.verified_facts.append(text)
            self.verified_facts = self.verified_facts[-256:]

    def add_schema_report(self, report: SchemaEvidenceReport) -> None:
        self.schema_reports.append(report)
        self.schema_reports = self.schema_reports[-MAX_SCHEMA_REPORTS:]

    def add_join_report(self, report: JoinEvidenceReport) -> None:
        self.join_reports.append(report)
        self.join_reports = self.join_reports[-MAX_JOIN_REPORTS:]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentMemory":
        mem = cls()
        if not isinstance(data, dict):
            return mem
        mem.goal = str(data.get("goal") or "")
        mem.intent = str(data.get("intent") or "")
        mem.constraints = [str(x) for x in _list_or_empty(data.get("constraints"))]
        mem.confirmed_facts = [str(x) for x in _list_or_empty(data.get("confirmed_facts"))][-12:]
        mem.verified_facts = [str(x) for x in _list_or_empty(data.get("verified_facts"))][-256:]
        mem.excluded_paths = [
            _excluded_path_from_dict(x) for x in _list_or_empty(data.get("excluded_paths"))
            if isinstance(x, dict)
        ][-MAX_EXCLUDED:]
        mem.sql_artifacts = [
            _sql_artifact_from_dict(x) for x in _list_or_empty(data.get("sql_artifacts"))
            if isinstance(x, dict)
        ][-MAX_SQL_ARTIFACTS:]
        mem.schema_reports = [
            _schema_report_from_dict(x) for x in _list_or_empty(data.get("schema_reports"))
            if isinstance(x, dict)
        ][-MAX_SCHEMA_REPORTS:]
        mem.join_reports = [
            _join_report_from_dict(x) for x in _list_or_empty(data.get("join_reports"))
            if isinstance(x, dict)
        ][-MAX_JOIN_REPORTS:]
        return mem


def next_prefixed_id(memory: AgentMemory, prefix: str, *, collections: tuple[str, ...] = ()) -> str:
    """Return a stable next id for compact memory artifacts.

    Scans the specified collections to find the highest existing index for the
    given prefix, then returns prefix + (max + 1).
    """
    ids: list[str] = []
    for name in collections:
        for item in getattr(memory, name, []) or []:
            ids.append(str(getattr(item, "id", "") or ""))
    return f"{prefix}{_next_index_from_ids(ids, prefix)}"


# ── Deserialization helpers ──────────────────────────────────────────


def _excluded_path_from_dict(data: dict[str, Any]) -> ExcludedPath:
    return ExcludedPath(
        target=str(data.get("target") or ""),
        reason=str(data.get("reason") or ""),
        evidence_ref=str(data.get("evidence_ref") or ""),
        source_priority=str(data.get("source_priority") or "evidence"),
    )


def _schema_candidate_from_dict(data: dict[str, Any]) -> SchemaCandidate:
    return SchemaCandidate(
        database=str(data.get("database") or ""),
        table=str(data.get("table") or ""),
        columns=[dict(x) for x in _list_or_empty(data.get("columns")) if isinstance(x, dict)],
        summary=str(data.get("summary") or ""),
        notes=dict(data.get("notes") or {}) if isinstance(data.get("notes"), dict) else {},
        status=str(data.get("status") or "active"),
        exclusion_reason=str(data.get("exclusion_reason") or ""),
        row_count=_optional_int(data.get("row_count")),
        indexes=_list_or_empty(data.get("indexes")),
        foreign_keys=_list_or_empty(data.get("foreign_keys")),
    )


def _schema_report_from_dict(data: dict[str, Any]) -> SchemaEvidenceReport:
    candidates = [
        _schema_candidate_from_dict(c) for c in _list_or_empty(data.get("candidates"))
        if isinstance(c, dict)
    ]
    return SchemaEvidenceReport(
        id=str(data.get("id") or ""),
        request=str(data.get("request") or ""),
        actions_taken=[str(x) for x in _list_or_empty(data.get("actions_taken"))],
        candidates=candidates,
        missing=[str(x) for x in _list_or_empty(data.get("missing"))],
        source_summary=str(data.get("source_summary") or ""),
    )


def _join_report_from_dict(data: dict[str, Any]) -> JoinEvidenceReport:
    return JoinEvidenceReport(
        id=str(data.get("id") or ""),
        request=str(data.get("request") or ""),
        tables=[str(x) for x in _list_or_empty(data.get("tables"))],
        actions_taken=[str(x) for x in _list_or_empty(data.get("actions_taken"))],
        relations=[dict(x) for x in _list_or_empty(data.get("relations")) if isinstance(x, dict)],
        source_summary=str(data.get("source_summary") or ""),
        warnings=[str(x) for x in _list_or_empty(data.get("warnings"))],
    )


def _sql_artifact_from_dict(data: dict[str, Any]) -> SQLArtifact:
    return SQLArtifact(
        id=str(data.get("id") or ""),
        purpose=str(data.get("purpose") or ""),
        sql=str(data.get("sql") or ""),
        database=str(data.get("database") or ""),
        row_count=_int_or_zero(data.get("row_count")),
        columns=[str(x) for x in _list_or_empty(data.get("columns"))],
        rows_preview=[dict(x) for x in _list_or_empty(data.get("rows_preview")) if isinstance(x, dict)],
        result_summary=str(data.get("result_summary") or ""),
        warnings=[str(x) for x in _list_or_empty(data.get("warnings"))],
        truncated=bool(data.get("truncated")),
    )


# ── Primitives ───────────────────────────────────────────────────────


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _list_or_empty(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _next_index_from_ids(ids: list[str], prefix: str) -> int:
    high = 0
    for item in ids:
        text = str(item or "")
        if text[:len(prefix)] != prefix:
            continue
        try:
            high = max(high, int(text[len(prefix):]))
        except Exception:
            continue
    return high + 1
