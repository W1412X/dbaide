"""Per-run state of a single Ask execution.

One ``RunState`` holds everything the tool loop accumulates while answering one
question — the discovered/resolved schema, the draft SQL and its confidence, the
pending clarification, confirmed business criteria, etc. It is created fresh at
the start of every run (``AskOrchestrator._reset_loop_state``) and serialized for
pause/resume (``dbaide.agent.loop_state``).

Grouping these into one typed object (instead of ~two dozen ``_loop_*`` attributes
scattered on the orchestrator) gives the run state a single home, an explicit
field list, and a clear lifecycle — the components that read/write it now go
through ``orchestrator.run_state.<field>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dbaide.models import ColumnInfo


@dataclass
class RunState:
    # Inputs for this run.
    question: str = ""
    database: str = ""
    execute_allowed: bool = False

    # Schema discovery / disclosure accumulated during the loop.
    discovery: Any = None                                   # DiscoveryResult | None
    table: str = ""
    table_database: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)
    schemas: dict[str, list[ColumnInfo]] = field(default_factory=dict)
    schema_db: dict[str, str] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)
    resolved_schema: Any = None                             # ResolvedSchema (minimal-necessary) | None

    # Trace: node id of the tool step currently running (for nested traces).
    trace_node: str = ""

    # SQL draft + outcome.
    sql: str = ""
    sql_rationale: str = ""
    sql_confidence: float | None = None                     # None = no SQL generated yet (neutral)
    sql_feedback: str = ""
    query_result: Any = None                                # QueryResult | None
    answer: str = ""

    # Pause/resume: a pending clarification awaiting the user.
    pending_question: str = ""
    pending_options: list[Any] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)

    # Confirmed business criteria (口径) injected into SQL, and the questions
    # currently awaiting a reply (paired with the criteria on resume).
    clarifications: list[str] = field(default_factory=list)
    clarify_questions: str = ""

    fail_reason: str = ""
    # The pinned scope (attachments) prioritises the FIRST discovery only; a later
    # discovery in the same run broadens, so a wrong/insufficient pin can't trap the
    # agent into searching only the attached scope forever.
    scope_used: bool = False
