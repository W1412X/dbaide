"""Join evidence retrieval for the single-brain Ask agent.

This is deliberately separate from schema evidence. It answers one narrow
question: "what relation candidates exist between these tables, and how well are
they supported?" It reads user-saved joins, declared foreign keys, semantic
candidates when requested, and sample validation evidence. The main loop decides
which relation, if any, belongs in the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbaide.agent.memory import JoinEvidenceReport
from dbaide.agent.progress_events import child_node, subagent_event
from dbaide.agent.schema_context import collect_relations, disclosed_schemas_for_tables, normalize_db_table

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator


@dataclass(slots=True)
class JoinContextReport:
    id: str
    request: str
    tables: list[tuple[str, str]] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    source_summary: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_memory_report(self) -> JoinEvidenceReport:
        return JoinEvidenceReport(
            id=self.id,
            request=self.request,
            tables=[_label(db, table) for db, table in self.tables],
            actions_taken=list(self.actions_taken),
            relations=list(self.relations),
            source_summary=self.source_summary,
            warnings=list(self.warnings),
        )

    def to_tool_data(self) -> dict[str, Any]:
        return {
            "report_id": self.id,
            "request": self.request,
            "tables": [_label(db, table) for db, table in self.tables],
            "actions_taken": list(self.actions_taken),
            "relations": list(self.relations),
            "source_summary": self.source_summary,
            "warnings": list(self.warnings),
            "instruction": (
                "This is join evidence, not a final join decision. User-saved joins are "
                "authoritative evidence with high confidence, but the main LLM still decides "
                "whether the relation matches the user's question and SQL grain."
            ),
        }


class JoinEvidenceRetriever:
    PARENT = "retrieve_join_context"

    def __init__(self, orchestrator: "AskOrchestrator") -> None:
        self.orch = orchestrator

    def retrieve(
        self,
        request: str,
        *,
        tables: list[str] | None = None,
        database: str = "",
        infer_semantic: bool = False,
        validate_sample: bool = False,
        sample_size: int = 150,
    ) -> JoinContextReport:
        base = self.orch.run_state.trace_node or self.PARENT
        report_id = f"join:{len(self.orch.run_state.memory.join_reports) + 1}"
        targets = self._targets(tables or [], database)
        actions: list[str] = []
        warnings: list[str] = []
        relations: list[dict[str, Any]] = []

        if len({table for _, table in targets}) < 2:
            warnings.append("Need at least two tables to retrieve join evidence.")
            report = JoinContextReport(
                id=report_id,
                request=request,
                tables=targets,
                actions_taken=actions,
                relations=[],
                source_summary="0 relation candidate(s); insufficient table count.",
                warnings=warnings,
            )
            self.orch.run_state.memory.add_join_report(report.to_memory_report())
            return report

        node = child_node(base, "join evidence")
        self.orch.progress(subagent_event(
            agent="join_validate",
            parent_id=base,
            node_id=node,
            title="Retrieve join evidence",
            status="running",
            detail=", ".join(_label(db, table) for db, table in targets),
        ))
        try:
            schemas = disclosed_schemas_for_tables(self.orch, targets)
            actions.append("loaded disclosed table schemas")
            relations = collect_relations(
                self.orch,
                targets,
                question=request or self.orch.run_state.question,
                disclosed_schemas=schemas,
                infer_semantic=infer_semantic,
                validate_sample=validate_sample,
                sample_size=sample_size,
                parent=node,
            )
            actions.append(
                "collected user catalog joins, declared foreign keys"
                + (", semantic candidates" if infer_semantic else "")
                + (", and sample validation" if validate_sample else "")
            )
        except Exception as exc:
            warnings.append(f"join evidence retrieval failed: {exc}")
            relations = []

        source_summary = _source_summary(relations)
        report = JoinContextReport(
            id=report_id,
            request=request,
            tables=targets,
            actions_taken=actions,
            relations=relations,
            source_summary=source_summary,
            warnings=warnings,
        )
        self.orch.run_state.relations = relations
        self.orch.run_state.memory.add_join_report(report.to_memory_report())
        self.orch.progress(subagent_event(
            agent="join_validate",
            parent_id=base,
            node_id=node,
            title="Retrieve join evidence",
            status="completed" if not warnings else "info",
            detail=source_summary,
        ))
        return report

    def _targets(self, tables: list[str], database: str) -> list[tuple[str, str]]:
        db_default = database or self.orch.run_state.table_database or self.orch.run_state.database or ""
        targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw in tables:
            db, table = normalize_db_table(str(raw or ""), db_default)
            if table:
                key = (db, table)
                if key not in seen:
                    seen.add(key)
                    targets.append(key)
        if targets:
            return targets
        for key in self.orch.run_state.schemas:
            db = self.orch.run_state.schema_db.get(key, db_default)
            prefix = f"{db}."
            table = key[len(prefix):] if db and key.startswith(prefix) else key
            pair = (db, table)
            if pair not in seen:
                seen.add(pair)
                targets.append(pair)
        return targets


def _label(database: str, table: str) -> str:
    return f"{database}.{table}" if database else table


def _source_summary(relations: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for rel in relations:
        source = str(rel.get("source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    parts = [f"{source}={count}" for source, count in sorted(counts.items())]
    return f"{len(relations)} relation candidate(s)" + (f"; {', '.join(parts)}" if parts else "")
