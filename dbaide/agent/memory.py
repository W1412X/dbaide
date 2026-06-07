"""Compressed working memory for the Ask agent.

The loop uses one LLM as the only decision maker. Tools gather evidence; this
module keeps a compact, action-oriented memory of what has already been tried,
what it produced, what was excluded, and which artifacts can be reused. The
prompt should show the model the work history and current evidence, not dump raw
tool output every round.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


MAX_WORK_STEPS = 18
MAX_FINDINGS = 18
MAX_OPEN_QUESTIONS = 8
MAX_EXCLUDED = 12
MAX_SCHEMA_REPORTS = 5
MAX_JOIN_REPORTS = 5
MAX_SQL_ARTIFACTS = 8
MAX_RESOLVED_QUESTIONS = 12
MAX_DO_NOT_REPEAT = 18


@dataclass(slots=True)
class WorkStep:
    id: str
    action: str
    purpose: str = ""
    input_summary: str = ""
    result_summary: str = ""
    judgment: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    status: str = "completed"


@dataclass(slots=True)
class Finding:
    text: str
    source: str = ""
    confidence: str = "observed"


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
    joins: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
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


@dataclass(slots=True)
class AgentMemory:
    goal: str = ""
    intent: str = ""
    constraints: list[str] = field(default_factory=list)
    work_log: list[WorkStep] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    excluded_paths: list[ExcludedPath] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    schema_reports: list[SchemaEvidenceReport] = field(default_factory=list)
    join_reports: list[JoinEvidenceReport] = field(default_factory=list)
    sql_artifacts: list[SQLArtifact] = field(default_factory=list)
    confirmed_facts: list[str] = field(default_factory=list)
    pending_confirmations: list[str] = field(default_factory=list)
    action_ledger: list[str] = field(default_factory=list)
    resolved_questions: list[str] = field(default_factory=list)
    do_not_repeat: list[str] = field(default_factory=list)
    next_action_hint: str = ""

    def reset_goal(self, question: str, *, database: str = "", execute_allowed: bool = True) -> None:
        self.goal = str(question or "").strip()
        self.intent = "Answer one independent database question."
        self.constraints = [
            f"Database scope: {database or '(any)'}",
            f"SQL execution: {'allowed' if execute_allowed else 'disabled'}",
        ]

    def record_work(
        self,
        *,
        action: str,
        args: dict[str, Any] | None = None,
        ok: bool = True,
        summary: str = "",
        artifacts: list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        step_id = f"w{len(self.work_log) + 1}"
        input_summary = _compact_json(args or {}, limit=360)
        result_summary = _trim(summary, 700)
        self.work_log.append(WorkStep(
            id=step_id,
            action=action,
            input_summary=input_summary,
            result_summary=result_summary,
            status="completed" if ok else "failed",
            artifact_refs=list(artifacts or []),
        ))
        self.work_log = self.work_log[-MAX_WORK_STEPS:]
        ledger_key = f"{action}:{input_summary}"
        if ledger_key not in self.action_ledger:
            self.action_ledger.append(ledger_key)
            self.action_ledger = self.action_ledger[-MAX_WORK_STEPS:]
        self.add_do_not_repeat(ledger_key, f"{'completed' if ok else 'failed'}: {result_summary}")
        if ok and isinstance(data, dict):
            self.learn_tool_result(action=action, args=args or {}, data=data, summary=result_summary)

    def add_do_not_repeat(self, key: str, reason: str = "") -> None:
        key = _trim(key, 420)
        reason = _trim(reason, 260)
        if not key:
            return
        entry = f"{key} -> {reason}" if reason else key
        normalized_key = key.lower()
        self.do_not_repeat = [x for x in self.do_not_repeat if not x.lower().startswith(normalized_key)]
        self.do_not_repeat.append(entry)
        self.do_not_repeat = self.do_not_repeat[-MAX_DO_NOT_REPEAT:]

    def add_finding(self, text: str, *, source: str = "", confidence: str = "observed") -> None:
        text = _trim(text, 500)
        if not text:
            return
        key = text.lower()
        if any(f.text.lower() == key for f in self.findings):
            return
        self.findings.append(Finding(text=text, source=source, confidence=confidence))
        self.findings = self.findings[-MAX_FINDINGS:]

    def add_open_question(self, text: str) -> None:
        text = _trim(text, 400)
        if text and text not in self.open_questions:
            self.open_questions.append(text)
            self.open_questions = self.open_questions[-MAX_OPEN_QUESTIONS:]

    def resolve_open_question(self, text: str) -> None:
        if not text:
            return
        needle = text.strip().lower()
        removed = [q for q in self.open_questions if q.strip().lower() == needle]
        self.open_questions = [q for q in self.open_questions if q.strip().lower() != needle]
        for q in removed:
            self.add_resolved_question(q, "answered by user/tool evidence")

    def add_resolved_question(self, question: str, resolution: str) -> None:
        question = _trim(question, 300)
        resolution = _trim(resolution, 360)
        if not question:
            return
        entry = f"{question} -> {resolution}" if resolution else question
        if entry not in self.resolved_questions:
            self.resolved_questions.append(entry)
            self.resolved_questions = self.resolved_questions[-MAX_RESOLVED_QUESTIONS:]

    def add_exclusion(self, target: str, reason: str, *, evidence_ref: str = "", source_priority: str = "evidence") -> None:
        target = _trim(target, 180)
        reason = _trim(reason, 400)
        if not target or not reason:
            return
        key = (target.lower(), reason.lower())
        if any((e.target.lower(), e.reason.lower()) == key for e in self.excluded_paths):
            return
        self.excluded_paths.append(ExcludedPath(target, reason, evidence_ref, source_priority))
        self.excluded_paths = self.excluded_paths[-MAX_EXCLUDED:]

    def add_schema_report(self, report: SchemaEvidenceReport) -> None:
        self.schema_reports.append(report)
        self.schema_reports = self.schema_reports[-MAX_SCHEMA_REPORTS:]
        active = [c for c in report.candidates if c.status == "active"]
        excluded = [c for c in report.candidates if c.status != "active"]
        self.add_finding(
            f"Schema report {report.id}: {len(active)} active candidate table(s), "
            f"{len(excluded)} excluded/deprecated candidate(s), {len(report.conflicts)} conflict(s).",
            source=report.id,
        )
        for c in excluded:
            self.add_exclusion(
                f"{c.database}.{c.table}" if c.database else c.table,
                c.exclusion_reason or c.status,
                evidence_ref=report.id,
                source_priority="user_note" if c.notes else "evidence",
            )
        for conflict in report.conflicts:
            reason = str(conflict.get("reason") or conflict.get("type") or "").strip()
            if reason:
                self.add_open_question(f"Schema ambiguity in {report.id}: {reason}")

    def add_join_report(self, report: JoinEvidenceReport) -> None:
        self.join_reports.append(report)
        self.join_reports = self.join_reports[-MAX_JOIN_REPORTS:]
        sources = sorted({str(r.get("source") or "unknown") for r in report.relations})
        self.add_finding(
            f"Join report {report.id}: {len(report.relations)} relation candidate(s) for "
            f"{', '.join(report.tables)}; sources={', '.join(sources) or 'none'}.",
            source=report.id,
        )

    def add_sql_artifact(self, artifact: SQLArtifact) -> None:
        self.sql_artifacts.append(artifact)
        self.sql_artifacts = self.sql_artifacts[-MAX_SQL_ARTIFACTS:]
        self.add_finding(
            f"SQL artifact {artifact.id}: {artifact.row_count} row(s), columns={', '.join(artifact.columns[:8])}. "
            f"{artifact.result_summary}",
            source=artifact.id,
        )

    def learn_tool_result(self, *, action: str, args: dict[str, Any], data: dict[str, Any],
                          summary: str = "") -> None:
        if action == "describe_table":
            self._learn_described_table(args, data)
        elif action in {"retrieve_schema_context", "discover_schema"}:
            self._learn_schema_result(data)
        elif action in {"execute_sql", "execute_readonly_sql"}:
            sql = str(data.get("sql") or args.get("sql") or "").strip()
            rows = data.get("row_count")
            self.add_finding(
                f"Executed SQL returned {rows if rows is not None else '?'} row(s): {_trim(sql, 220)}",
                source=str(data.get("artifact_id") or action),
            )

    def _learn_described_table(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        database = str(data.get("database") or args.get("database") or "").strip()
        table = str(data.get("table") or args.get("table") or "").strip()
        label = f"{database}.{table}" if database else table
        columns = _column_names(data.get("columns") or [])
        if not label or not columns:
            return
        key_cols = _key_columns(columns)
        self.add_finding(
            f"Described {label}: columns include {', '.join(key_cols[:18])}; "
            f"indexes={len(data.get('indexes') or [])}, fks={len(data.get('foreign_keys') or [])}.",
            source=f"describe_table:{label}",
        )
        self._resolve_questions_from_columns(label, columns)

    def _learn_schema_result(self, data: dict[str, Any]) -> None:
        report_id = str(data.get("report_id") or "").strip()
        candidates = [c for c in data.get("candidates") or [] if isinstance(c, dict)]
        if not candidates:
            return
        active_labels: list[str] = []
        noted_labels: list[str] = []
        for c in candidates[:10]:
            label = _table_label(c)
            if not label:
                continue
            if str(c.get("status") or "active") == "active":
                active_labels.append(label)
            notes = c.get("notes") if isinstance(c.get("notes"), dict) else {}
            if notes and str(notes.get("table") or "").strip():
                noted_labels.append(f"{label}: {_trim(str(notes.get('table')), 120)}")
        if active_labels:
            self.add_finding(
                f"Schema evidence {report_id or ''} active candidates: {', '.join(active_labels[:8])}.",
                source=report_id or "schema",
            )
        for item in noted_labels:
            self.add_finding(f"User-note schema evidence: {item}", source=report_id or "schema")

    def _resolve_questions_from_columns(self, label: str, columns: list[str]) -> None:
        table = label.split(".")[-1].lower()
        normalized_cols = {c.lower() for c in columns}
        remaining: list[str] = []
        for question in self.open_questions:
            q = question.lower()
            mentions_table = table and table in q
            if label.lower() in q:
                mentions_table = True
            resolved_by = ""
            if mentions_table and any(token in q for token in ("是否包含", "是否存在", "contains", "has")):
                if any(token in q for token in ("妥投", "deliver")) and _has_semantic_column(normalized_cols, ("deliver", "delivered")):
                    resolved_by = f"{label} has delivered-related column(s): {', '.join(_matching_columns(columns, ('deliver', 'delivered'))[:6])}"
                elif any(token in q for token in ("国家", "country")) and _has_semantic_column(normalized_cols, ("country",)):
                    resolved_by = f"{label} has country column(s): {', '.join(_matching_columns(columns, ('country',))[:6])}"
                elif any(token in q for token in ("退款", "refund")) and _has_semantic_column(normalized_cols, ("refund",)):
                    resolved_by = f"{label} has refund column(s): {', '.join(_matching_columns(columns, ('refund',))[:6])}"
                elif any(token in q for token in ("时间", "日期", "date", "time")) and _has_time_column(normalized_cols):
                    resolved_by = f"{label} has time/date column(s): {', '.join(_time_columns(columns)[:6])}"
            if resolved_by:
                self.add_resolved_question(question, resolved_by)
            else:
                remaining.append(question)
        self.open_questions = remaining

    def prompt_block(self) -> str:
        lines: list[str] = []
        lines += ["[Goal]", self.goal or "(unknown)", ""]
        if self.constraints:
            lines += ["[Constraints]", *[f"- {c}" for c in self.constraints], ""]
        if self.confirmed_facts:
            lines += ["[Authoritative Facts]", *[f"- {x}" for x in self.confirmed_facts[-12:]], ""]
        if self.work_log:
            lines += ["[Work Done]"]
            for step in self.work_log[-12:]:
                refs = f" refs={', '.join(step.artifact_refs)}" if step.artifact_refs else ""
                lines.append(f"- {step.id} {step.action} {step.status}{refs}: {step.result_summary}")
            lines.append("")
        if self.findings:
            lines += ["[Current Evidence]", *[f"- {f.text} ({f.source})" if f.source else f"- {f.text}" for f in self.findings[-12:]], ""]
        if self.resolved_questions:
            lines += ["[Resolved Questions]", *[f"- {q}" for q in self.resolved_questions[-MAX_RESOLVED_QUESTIONS:]], ""]
        if self.schema_reports:
            lines += ["[Schema Evidence]"]
            for report in self.schema_reports[-3:]:
                cand = []
                for c in report.candidates[:8]:
                    label = f"{c.database}.{c.table}" if c.database else c.table
                    bits = [label, c.status]
                    if c.notes.get("table"):
                        bits.append(f"note={_trim(str(c.notes['table']), 90)}")
                    if c.exclusion_reason:
                        bits.append(f"excluded={_trim(c.exclusion_reason, 90)}")
                    if c.row_count is not None:
                        bits.append(f"rows~{c.row_count}")
                    if c.indexes:
                        bits.append(f"indexes={len(c.indexes)}")
                    if c.foreign_keys:
                        bits.append(f"declared_fk={len(c.foreign_keys)}")
                    cand.append(" / ".join(bits))
                lines.append(f"- {report.id}: " + "; ".join(cand))
                if report.conflicts:
                    lines.append(f"  conflicts: {_compact_json(report.conflicts[:4], limit=600)}")
            lines.append("")
        if self.join_reports:
            lines += ["[Join Evidence]"]
            for report in self.join_reports[-3:]:
                lines.append(
                    f"- {report.id}: tables={', '.join(report.tables)}; "
                    f"{len(report.relations)} candidate relation(s); {report.source_summary}"
                )
                for rel in report.relations[:5]:
                    left = f"{rel.get('table')}.{rel.get('column')}"
                    right = f"{rel.get('ref_table')}.{rel.get('ref_column')}"
                    lines.append(
                        f"  - {left} -> {right}; source={rel.get('source')}; "
                        f"confidence={rel.get('confidence')}; reason={_trim(str(rel.get('reason') or ''), 100)}"
                    )
            lines.append("")
        if self.sql_artifacts:
            lines += ["[SQL Artifacts]"]
            for art in self.sql_artifacts[-5:]:
                lines.append(
                    f"- {art.id}: purpose={art.purpose or '(not stated)'} rows={art.row_count} "
                    f"columns={', '.join(art.columns[:8])} sql={_trim(art.sql, 220)}"
                )
            lines.append("")
        if self.open_questions:
            lines += ["[Open Issues]", *[f"- {q}" for q in self.open_questions[-MAX_OPEN_QUESTIONS:]], ""]
        if self.excluded_paths:
            lines += ["[Do Not Repeat Unless New Evidence Changes It]"]
            for item in self.excluded_paths[-MAX_EXCLUDED:]:
                lines.append(f"- {item.target}: {item.reason} ({item.source_priority}; {item.evidence_ref})")
            lines.append("")
        if self.do_not_repeat:
            lines += ["[Completed / Blocked Tool Calls — Do Not Repeat Exactly]"]
            lines.extend(f"- {x}" for x in self.do_not_repeat[-MAX_DO_NOT_REPEAT:])
            lines.append("")
        if self.action_ledger:
            lines += ["[Recent Action Ledger]", *[f"- {x}" for x in self.action_ledger[-10:]], ""]
        if self.next_action_hint:
            lines += ["[Last Suggested Next Step]", self.next_action_hint, ""]
        return "\n".join(lines).strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentMemory":
        mem = cls()
        if not isinstance(data, dict):
            return mem
        mem.goal = str(data.get("goal") or "")
        mem.intent = str(data.get("intent") or "")
        mem.constraints = [str(x) for x in data.get("constraints") or []]
        mem.work_log = [WorkStep(**x) for x in data.get("work_log") or [] if isinstance(x, dict)]
        mem.findings = [Finding(**x) for x in data.get("findings") or [] if isinstance(x, dict)]
        mem.open_questions = [str(x) for x in data.get("open_questions") or []]
        mem.excluded_paths = [ExcludedPath(**x) for x in data.get("excluded_paths") or [] if isinstance(x, dict)]
        mem.hypotheses = [str(x) for x in data.get("hypotheses") or []]
        mem.schema_reports = [_schema_report_from_dict(x) for x in data.get("schema_reports") or [] if isinstance(x, dict)]
        mem.join_reports = [JoinEvidenceReport(**x) for x in data.get("join_reports") or [] if isinstance(x, dict)]
        mem.sql_artifacts = [SQLArtifact(**x) for x in data.get("sql_artifacts") or [] if isinstance(x, dict)]
        mem.confirmed_facts = [str(x) for x in data.get("confirmed_facts") or []]
        mem.pending_confirmations = [str(x) for x in data.get("pending_confirmations") or []]
        mem.action_ledger = [str(x) for x in data.get("action_ledger") or []]
        mem.resolved_questions = [str(x) for x in data.get("resolved_questions") or []]
        mem.do_not_repeat = [str(x) for x in data.get("do_not_repeat") or []]
        mem.next_action_hint = str(data.get("next_action_hint") or "")
        return mem


def _schema_report_from_dict(data: dict[str, Any]) -> SchemaEvidenceReport:
    candidates = [
        SchemaCandidate(**c) for c in data.get("candidates") or []
        if isinstance(c, dict)
    ]
    return SchemaEvidenceReport(
        id=str(data.get("id") or ""),
        request=str(data.get("request") or ""),
        actions_taken=[str(x) for x in data.get("actions_taken") or []],
        candidates=candidates,
        joins=list(data.get("joins") or []),
        conflicts=list(data.get("conflicts") or []),
        missing=[str(x) for x in data.get("missing") or []],
        source_summary=str(data.get("source_summary") or ""),
    )


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _compact_json(data: Any, *, limit: int) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    return _trim(text, limit)


def _column_names(columns: Any) -> list[str]:
    out: list[str] = []
    for col in columns or []:
        if isinstance(col, dict):
            name = str(col.get("name") or "").strip()
        else:
            name = str(getattr(col, "name", "") or "").strip()
        if name:
            out.append(name)
    return out


def _key_columns(columns: list[str]) -> list[str]:
    priority = (
        "id", "spu", "sku", "country", "date", "dt", "delivered", "refund",
        "quantity", "order", "created", "updated", "at",
    )
    selected = [c for c in columns if any(p in c.lower() for p in priority)]
    return selected or columns


def _table_label(candidate: dict[str, Any]) -> str:
    database = str(candidate.get("database") or "").strip()
    table = str(candidate.get("table") or "").strip()
    if not table:
        return ""
    return f"{database}.{table}" if database else table


def _has_semantic_column(columns: set[str], needles: tuple[str, ...]) -> bool:
    return bool(_matching_columns(list(columns), needles))


def _matching_columns(columns: list[str], needles: tuple[str, ...]) -> list[str]:
    return [c for c in columns if any(needle in c.lower() for needle in needles)]


def _has_time_column(columns: set[str]) -> bool:
    return bool(_time_columns(list(columns)))


def _time_columns(columns: list[str]) -> list[str]:
    out: list[str] = []
    for c in columns:
        low = c.lower()
        if low in {"dt", "date"} or low.endswith("_at") or "date" in low or "time" in low:
            out.append(c)
    return out
