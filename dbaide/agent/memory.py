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


MAX_WORK_STEPS = 512
MAX_FINDINGS = 512
MAX_OPEN_QUESTIONS = 128
MAX_EXCLUDED = 256
MAX_SCHEMA_REPORTS = 64
MAX_JOIN_REPORTS = 64
MAX_SQL_ARTIFACTS = 128
MAX_RESOLVED_QUESTIONS = 128
MAX_DO_NOT_REPEAT = 0  # unused — repetition is governed by the outer step budget only
MAX_ARCHIVE_INDEX = 128
PROMPT_SLICE_WORK = 48
PROMPT_SLICE_FINDINGS = 48
PROMPT_SLICE_SCHEMA = 12
PROMPT_SLICE_JOIN = 12
PROMPT_SLICE_SQL = 24
PROMPT_SLICE_ARCHIVE = 48
PROMPT_SLICE_FACTS = 48
PROMPT_SLICE_LEDGER = 48


@dataclass(slots=True)
class WorkStep:
    id: str
    action: str
    purpose: str = ""
    input_summary: str = ""
    result_summary: str = ""
    judgment: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    raw_ref: str = ""
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
class MemoryArchiveItem:
    id: str
    action: str
    summary: str = ""
    source_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


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
    archive: list[MemoryArchiveItem] = field(default_factory=list)
    next_work_index: int = 1
    next_archive_index: int = 1

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
        step_id = f"w{self.next_work_index}"
        self.next_work_index += 1
        input_summary = _compact_json(args or {}, limit=360)
        result_summary = _trim(summary, 700)
        artifact_refs = list(artifacts or [])
        raw_ref = self.archive_raw(
            action=action,
            summary=result_summary,
            source_refs=[step_id, *artifact_refs],
            payload={
                "work_step": step_id,
                "action": action,
                "args": args or {},
                "ok": ok,
                "summary": summary,
                "artifact_refs": artifact_refs,
                "data": data if isinstance(data, dict) else {},
            },
        )
        self.work_log.append(WorkStep(
            id=step_id,
            action=action,
            input_summary=input_summary,
            result_summary=result_summary,
            status="completed" if ok else "failed",
            artifact_refs=artifact_refs,
            raw_ref=raw_ref,
        ))
        self.work_log = self.work_log[-MAX_WORK_STEPS:]
        ledger_key = f"{action}:{input_summary}"
        if ledger_key not in self.action_ledger:
            self.action_ledger.append(ledger_key)
            self.action_ledger = self.action_ledger[-MAX_WORK_STEPS:]
        if ok and isinstance(data, dict):
            self.learn_tool_result(action=action, args=args or {}, data=data, summary=result_summary)

    def archive_raw(
        self,
        *,
        action: str,
        summary: str = "",
        source_refs: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Store original evidence outside the prompt and return a stable ref.

        The prompt sees only compact summaries plus this id. The agent can call
        retrieve_memory_item(ref=...) to inspect the full payload when the summary is
        insufficient. Archive entries are intentionally not trimmed with the prompt
        sections; losing raw evidence would make compression non-auditable.
        """
        ref = f"mem:{self.next_archive_index}"
        self.next_archive_index += 1
        refs = [str(x).strip() for x in (source_refs or []) if str(x).strip()]
        self.archive.append(MemoryArchiveItem(
            id=ref,
            action=str(action or "").strip(),
            summary=_trim(summary, 500),
            source_refs=refs,
            payload=payload or {},
        ))
        return ref

    def retrieve_archive(self, ref: str) -> MemoryArchiveItem | None:
        needle = str(ref or "").strip()
        if not needle:
            return None
        for item in reversed(self.archive):
            if item.id == needle or needle in item.source_refs:
                return item
        return None

    def add_do_not_repeat(self, key: str, reason: str = "") -> None:
        if MAX_DO_NOT_REPEAT <= 0:
            return
        key = _trim(key, 420)
        reason = _trim(reason, 260)
        if not key:
            return
        entry = f"{key} -> {reason}" if reason else key
        normalized_key = key.lower()
        self.do_not_repeat = [
            x for x in self.do_not_repeat
            if x.lower().split(" -> ", 1)[0] != normalized_key
        ]
        self.do_not_repeat.append(entry)
        self.do_not_repeat = self.do_not_repeat[-MAX_DO_NOT_REPEAT:]

    def add_hypothesis(self, text: str) -> None:
        text = _trim(text, 500)
        if text and text not in self.hypotheses:
            self.hypotheses.append(text)
            self.hypotheses = self.hypotheses[-MAX_FINDINGS:]

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
            f"{len(excluded)} inactive/missing candidate(s).",
            source=report.id,
        )
        for c in excluded:
            self.add_exclusion(
                f"{c.database}.{c.table}" if c.database else c.table,
                c.exclusion_reason or c.status,
                evidence_ref=report.id,
                source_priority="user_note" if c.notes else "evidence",
            )
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
        elif action == "inspect_metadata":
            self._learn_metadata_result(data)
        elif action in {"retrieve_schema_context", "discover_schema"}:
            self._learn_schema_result(data)
        elif action == "column_stats":
            self._learn_column_stats(args, data)
        elif action == "profile_table":
            self._learn_profile_table(args, data)
        elif action == "validate_joins":
            self._learn_validated_joins(data)
        elif action in {"execute_sql", "execute_readonly_sql"}:
            if data.get("pending") or data.get("blocked"):
                return
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

    def _learn_schema_result(self, data: dict[str, Any]) -> None:
        report_id = str(data.get("report_id") or "").strip()
        candidates = [c for c in data.get("candidates") or [] if isinstance(c, dict)]
        hits = [h for h in data.get("hits") or [] if isinstance(h, dict)]
        if hits and not candidates:
            labels: list[str] = []
            noted_labels: list[str] = []
            for hit in hits[:12]:
                database = str(hit.get("database") or "").strip()
                table = str(hit.get("table") or hit.get("name") or "").strip()
                path = str(hit.get("path") or "").strip()
                label = f"{database}.{table}" if database and table else (path or table)
                if label:
                    labels.append(label)
                    note = str(hit.get("note") or "").strip()
                    if note:
                        noted_labels.append(f"{label}: {_trim(note, 120)}")
            if labels:
                self.add_finding(
                    f"Schema discovery found: {', '.join(labels[:10])}.",
                    source=report_id or "discover_schema",
                )
            for item in noted_labels[:8]:
                self.add_finding(f"User-note schema discovery hit: {item}", source=report_id or "discover_schema")
            return
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

    def _learn_metadata_result(self, data: dict[str, Any]) -> None:
        tables = [t for t in data.get("tables") or [] if isinstance(t, dict)]
        matched_columns = [c for c in data.get("matched_columns") or [] if isinstance(c, dict)]
        labels = [_table_label(t) for t in tables[:8]]
        bits: list[str] = []
        if labels:
            bits.append(f"tables={', '.join([x for x in labels if x][:8])}")
        if matched_columns:
            cols = []
            for col in matched_columns[:10]:
                label = _table_label(col)
                name = str(col.get("name") or "").strip()
                if label and name:
                    cols.append(f"{label}.{name}")
            if cols:
                bits.append(f"matched_columns={', '.join(cols)}")
        if bits:
            self.add_finding(
                f"Metadata inspection: {'; '.join(bits)}.",
                source="inspect_metadata",
            )

    def _learn_column_stats(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        database = str(data.get("database") or args.get("database") or "").strip()
        table = str(data.get("table") or args.get("table") or "").strip()
        label = f"{database}.{table}" if database else table
        columns = [c for c in data.get("columns") or [] if isinstance(c, dict)]
        if not label or not columns:
            return
        bits: list[str] = []
        for col in columns[:8]:
            name = str(col.get("column") or "").strip()
            stats = col.get("stats") if isinstance(col.get("stats"), dict) else {}
            if not name or not stats:
                continue
            metric_bits: list[str] = []
            for key in ("null_rate", "distinct_count", "min", "max", "avg", "min_length", "max_length"):
                if key in stats:
                    metric_bits.append(f"{key}={_trim(str(stats.get(key)), 60)}")
            top = stats.get("top_values")
            if isinstance(top, list) and top:
                vals = []
                for item in top[:4]:
                    if isinstance(item, dict):
                        vals.append(f"{_trim(str(item.get('value')), 40)}:{item.get('count')}")
                if vals:
                    metric_bits.append("top_values=" + ", ".join(vals))
            if metric_bits:
                bits.append(f"{name} ({'; '.join(metric_bits[:8])})")
        if bits:
            self.add_finding(
                f"Column stats for {label}: " + " | ".join(bits),
                source=f"column_stats:{label}",
            )

    def _learn_profile_table(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        database = str(data.get("database") or args.get("database") or "").strip()
        table = str(data.get("table") or args.get("table") or "").strip()
        label = f"{database}.{table}" if database else table
        profiles = [p for p in data.get("profiles") or [] if isinstance(p, dict)]
        if not label or not profiles:
            return
        bits: list[str] = []
        for profile in profiles[:8]:
            column = str(profile.get("column") or "").strip()
            if not column:
                continue
            metrics: list[str] = []
            for key in ("row_count", "null_count", "distinct_count", "min_value", "max_value"):
                if profile.get(key) is not None:
                    metrics.append(f"{key}={_trim(str(profile.get(key)), 60)}")
            top = profile.get("top_values")
            if isinstance(top, list) and top:
                values = []
                for item in top[:4]:
                    if isinstance(item, dict):
                        values.append(f"{_trim(str(item.get('value')), 40)}:{item.get('count')}")
                if values:
                    metrics.append("top_values=" + ", ".join(values))
            if metrics:
                bits.append(f"{column} ({'; '.join(metrics[:8])})")
        if bits:
            self.add_finding(
                f"Profile for {label}: " + " | ".join(bits),
                source=f"profile_table:{label}",
            )

    def _learn_validated_joins(self, data: dict[str, Any]) -> None:
        relations = [r for r in data.get("relations") or [] if isinstance(r, dict)]
        if not relations:
            return
        bits: list[str] = []
        for rel in relations[:6]:
            left = f"{rel.get('table')}.{rel.get('column')}"
            right = f"{rel.get('ref_table')}.{rel.get('ref_column')}"
            confidence = rel.get("confidence")
            suffix = f" conf={confidence}" if confidence is not None else ""
            validation = rel.get("validation") if isinstance(rel.get("validation"), dict) else {}
            match_rate = validation.get("match_rate")
            if match_rate is not None:
                suffix += f" match_rate={match_rate}"
            bits.append(f"{left}->{right}{suffix}")
        self.add_finding(
            "Validated join evidence: " + "; ".join(bits),
            source="validate_joins",
        )

    def prompt_block(self) -> str:
        lines: list[str] = []
        lines += ["[Goal]", self.goal or "(unknown)", ""]
        if self.constraints:
            lines += ["[Constraints]", *[f"- {c}" for c in self.constraints], ""]
        if self.confirmed_facts:
            lines += ["[Authoritative Facts]", *[f"- {x}" for x in self.confirmed_facts[-PROMPT_SLICE_FACTS:]], ""]
        if self.hypotheses:
            lines += ["[Candidate Hypotheses]", *[f"- {x}" for x in self.hypotheses[-PROMPT_SLICE_FINDINGS:]], ""]
        if self.work_log:
            lines += ["[Work Done]"]
            for step in self.work_log[-PROMPT_SLICE_WORK:]:
                refs_list = [*step.artifact_refs]
                if step.raw_ref:
                    refs_list.append(f"raw={step.raw_ref}")
                refs = f" refs={', '.join(refs_list)}" if refs_list else ""
                lines.append(f"- {step.id} {step.action} {step.status}{refs}: {step.result_summary}")
            lines.append("")
        if self.findings:
            lines.append("[Observed Evidence / Model Working Notes]")
            for f in self.findings[-PROMPT_SLICE_FINDINGS:]:
                qualifier = f.confidence if f.confidence != "observed" else "observed"
                suffix_parts = [part for part in (f.source, qualifier) if part]
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                lines.append(f"- {f.text}{suffix}")
            lines.append("")
        if self.resolved_questions:
            lines += ["[Resolved Questions]", *[f"- {q}" for q in self.resolved_questions[-MAX_RESOLVED_QUESTIONS:]], ""]
        if self.schema_reports:
            lines += ["[Schema Evidence]"]
            for report in self.schema_reports[-PROMPT_SLICE_SCHEMA:]:
                cand = []
                for c in report.candidates[:8]:
                    label = f"{c.database}.{c.table}" if c.database else c.table
                    bits = [label, c.status]
                    if c.notes.get("table"):
                        bits.append(f"note={_trim(str(c.notes['table']), 90)}")
                    if c.exclusion_reason:
                        bits.append(f"excluded={_trim(c.exclusion_reason, 90)}")
                    col_names = _column_names(c.columns)
                    if col_names:
                        bits.append(f"cols={', '.join(_key_columns(col_names)[:10])}")
                    if c.row_count is not None:
                        bits.append(f"rows~{c.row_count}")
                    if c.indexes:
                        bits.append(f"indexes={len(c.indexes)}")
                    if c.foreign_keys:
                        bits.append(f"declared_fk={len(c.foreign_keys)}")
                    cand.append(" / ".join(bits))
                lines.append(f"- {report.id}: " + "; ".join(cand))
            lines.append("")
        if self.join_reports:
            lines += ["[Join Evidence]"]
            for report in self.join_reports[-PROMPT_SLICE_JOIN:]:
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
            for art in self.sql_artifacts[-PROMPT_SLICE_SQL:]:
                lines.append(
                    f"- {art.id}: purpose={art.purpose or '(not stated)'} rows={art.row_count} "
                    f"columns={', '.join(art.columns[:8])} sql={_trim(art.sql, 220)}"
                )
            lines.append("")
        if self.open_questions:
            lines += ["[Open Issues]", *[f"- {q}" for q in self.open_questions[-MAX_OPEN_QUESTIONS:]], ""]
        if self.excluded_paths:
            lines += ["[Excluded Paths]"]
            for item in self.excluded_paths[-MAX_EXCLUDED:]:
                lines.append(f"- {item.target}: {item.reason} ({item.source_priority}; {item.evidence_ref})")
            lines.append("")
        if self.action_ledger:
            lines += ["[Recent Action Ledger]", *[f"- {x}" for x in self.action_ledger[-PROMPT_SLICE_LEDGER:]], ""]
        if self.archive:
            lines += ["[Raw Evidence Archive]"]
            for item in self.archive[-PROMPT_SLICE_ARCHIVE:]:
                refs = f" refs={', '.join(item.source_refs[:6])}" if item.source_refs else ""
                lines.append(f"- {item.id} {item.action}{refs}: {item.summary}")
            lines.append("Use retrieve_memory_item(ref=...) when a compressed summary is insufficient.")
            lines.append("")
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
        mem.constraints = [str(x) for x in _list_or_empty(data.get("constraints"))]
        mem.work_log = [_work_step_from_dict(x) for x in _list_or_empty(data.get("work_log")) if isinstance(x, dict)][-MAX_WORK_STEPS:]
        mem.findings = [_finding_from_dict(x) for x in _list_or_empty(data.get("findings")) if isinstance(x, dict)][-MAX_FINDINGS:]
        mem.open_questions = [str(x) for x in _list_or_empty(data.get("open_questions"))][-MAX_OPEN_QUESTIONS:]
        mem.excluded_paths = [
            _excluded_path_from_dict(x) for x in _list_or_empty(data.get("excluded_paths")) if isinstance(x, dict)
        ][-MAX_EXCLUDED:]
        mem.hypotheses = [str(x) for x in _list_or_empty(data.get("hypotheses"))]
        mem.schema_reports = [
            _schema_report_from_dict(x) for x in _list_or_empty(data.get("schema_reports")) if isinstance(x, dict)
        ][-MAX_SCHEMA_REPORTS:]
        mem.join_reports = [
            _join_report_from_dict(x) for x in _list_or_empty(data.get("join_reports")) if isinstance(x, dict)
        ][-MAX_JOIN_REPORTS:]
        mem.sql_artifacts = [
            _sql_artifact_from_dict(x) for x in _list_or_empty(data.get("sql_artifacts")) if isinstance(x, dict)
        ][-MAX_SQL_ARTIFACTS:]
        mem.confirmed_facts = [str(x) for x in _list_or_empty(data.get("confirmed_facts"))][-12:]
        mem.pending_confirmations = [str(x) for x in _list_or_empty(data.get("pending_confirmations"))]
        mem.action_ledger = [str(x) for x in _list_or_empty(data.get("action_ledger"))][-MAX_WORK_STEPS:]
        mem.resolved_questions = [str(x) for x in _list_or_empty(data.get("resolved_questions"))][-MAX_RESOLVED_QUESTIONS:]
        mem.do_not_repeat = (
            [str(x) for x in _list_or_empty(data.get("do_not_repeat"))][-MAX_DO_NOT_REPEAT:]
            if MAX_DO_NOT_REPEAT > 0
            else []
        )
        mem.next_action_hint = str(data.get("next_action_hint") or "")
        mem.archive = [_archive_item_from_dict(x) for x in _list_or_empty(data.get("archive")) if isinstance(x, dict)]
        archive_work_refs = [
            ref
            for item in mem.archive
            for ref in item.source_refs
        ]
        inferred_work = _next_index_from_ids([x.id for x in mem.work_log] + archive_work_refs, "w")
        inferred_archive = _next_index_from_ids([x.id for x in mem.archive], "mem:")
        mem.next_work_index = max(
            _positive_int(data.get("next_work_index"), default=inferred_work),
            inferred_work,
        )
        mem.next_archive_index = _positive_int(
            data.get("next_archive_index"),
            default=inferred_archive,
        )
        mem.next_archive_index = max(mem.next_archive_index, inferred_archive)
        return mem


def _work_step_from_dict(data: dict[str, Any]) -> WorkStep:
    return WorkStep(
        id=str(data.get("id") or ""),
        action=str(data.get("action") or ""),
        purpose=str(data.get("purpose") or ""),
        input_summary=str(data.get("input_summary") or ""),
        result_summary=str(data.get("result_summary") or ""),
        judgment=str(data.get("judgment") or ""),
        artifact_refs=[str(x) for x in _list_or_empty(data.get("artifact_refs"))],
        raw_ref=str(data.get("raw_ref") or ""),
        status=str(data.get("status") or "completed"),
    )


def _finding_from_dict(data: dict[str, Any]) -> Finding:
    return Finding(
        text=str(data.get("text") or ""),
        source=str(data.get("source") or ""),
        confidence=str(data.get("confidence") or "observed"),
    )


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
    )


def _archive_item_from_dict(data: dict[str, Any]) -> MemoryArchiveItem:
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    return MemoryArchiveItem(
        id=str(data.get("id") or ""),
        action=str(data.get("action") or ""),
        summary=str(data.get("summary") or ""),
        source_refs=[str(x) for x in _list_or_empty(data.get("source_refs"))],
        payload=payload,
    )


def _positive_int(value: Any, *, default: int) -> int:
    try:
        out = int(value)
    except Exception:
        out = int(default)
    return max(1, out)


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


def _list_or_empty(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


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


def next_prefixed_id(memory: AgentMemory, prefix: str, *, collections: tuple[str, ...] = ()) -> str:
    """Return a stable next id for compact memory artifacts.

    Prompt-facing collections are trimmed, but raw archive refs are retained. ID
    generation therefore scans both current compact collections and archived refs
    so later evidence cannot reuse an older report/artifact id.
    """
    ids: list[str] = []
    for name in collections:
        for item in getattr(memory, name, []) or []:
            ids.append(str(getattr(item, "id", "") or ""))
    for item in getattr(memory, "archive", []) or []:
        ids.append(str(getattr(item, "id", "") or ""))
        ids.extend(str(ref or "") for ref in getattr(item, "source_refs", []) or [])
        payload = getattr(item, "payload", {}) or {}
        if isinstance(payload, dict):
            ids.extend(str(ref or "") for ref in payload.get("artifact_refs") or [])
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            if isinstance(data, dict):
                for key in ("report_id", "artifact_id"):
                    ids.append(str(data.get(key) or ""))
    return f"{prefix}{_next_index_from_ids(ids, prefix)}"


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
    seen: set[str] = set()
    out: list[str] = []
    for column in columns:
        if column not in seen:
            seen.add(column)
            out.append(column)
    return out


def _table_label(candidate: dict[str, Any]) -> str:
    database = str(candidate.get("database") or "").strip()
    table = str(candidate.get("table") or "").strip()
    if not table:
        return ""
    return f"{database}.{table}" if database else table
