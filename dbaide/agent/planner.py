from __future__ import annotations

from dataclasses import dataclass, field

from dbaide.context.catalog import ScoredTable
from dbaide.models import TaskType
from dbaide.tools.schema import SchemaTools


@dataclass(slots=True)
class Plan:
    task: TaskType
    question: str
    candidate_tables: list[ScoredTable] = field(default_factory=list)
    needs_execution: bool = False
    needs_profile: bool = False
    notes: list[str] = field(default_factory=list)


class Planner:
    def __init__(self, schema_tools: SchemaTools) -> None:
        self.schema_tools = schema_tools

    def plan(self, question: str, task: TaskType, *, database: str = "") -> Plan:
        candidates = self.schema_tools.candidate_tables(question, database=database, limit=6)
        needs_execution = task == TaskType.DATA_QUERY
        needs_profile = task == TaskType.DATA_PROFILE
        notes = ["Candidate tables matched by name/comment/alias (no vector search)."]
        if candidates:
            top = ", ".join(f"{c.table.name}({c.score:.1f})" for c in candidates[:3])
            notes.append(f"Top candidates: {top}")
        return Plan(task=task, question=question, candidate_tables=candidates, needs_execution=needs_execution, needs_profile=needs_profile, notes=notes)
