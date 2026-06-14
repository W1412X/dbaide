"""Per-run state of a single Ask execution.

One ``RunState`` holds everything the tool loop accumulates while answering one
question — discovered schema evidence, the draft SQL and its confidence, the
pending clarification, confirmed business criteria, etc. It is created fresh at
the start of every run (``AskOrchestrator._reset_loop_state``) and serialized for
pause/resume (``dbaide.agent.loop_state``).

Grouping these fields into one typed object gives the run state a single home,
an explicit field list, and a clear lifecycle — the components that read/write it
now go through ``orchestrator.run_state.<field>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dbaide.agent.memory import AgentMemory
from dbaide.models import ColumnInfo


@dataclass
class RunState:
    # Inputs for this run.
    question: str = ""
    database: str = ""
    execute_allowed: bool = False
    answer_language: str = "en"

    # Schema discovery / disclosure accumulated during the loop.
    discovery: Any = None                                   # DiscoveryResult | None
    table: str = ""
    table_database: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)
    schemas: dict[str, list[ColumnInfo]] = field(default_factory=dict)
    schema_db: dict[str, str] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)

    # Trace: node id of the tool step currently running (for nested traces).
    trace_node: str = ""

    # Compressed action-oriented working memory for the single-brain agent loop.
    memory: AgentMemory = field(default_factory=AgentMemory)

    # SQL draft + outcome.
    sql: str = ""
    sql_rationale: str = ""
    sql_confidence: float | None = None                     # None = no SQL generated yet (neutral)
    sql_feedback: str = ""
    query_result: Any = None                                # QueryResult | None
    answer: str = ""

    # Pending risk confirmation for a generated SQL. When the user approves, the SQL
    # hash is added to confirmed_risk_sqls so the next execute_sql call can run it.
    risk_confirmation: dict[str, Any] = field(default_factory=dict)
    confirmed_risk_sqls: list[str] = field(default_factory=list)

    # Pause/resume: a pending clarification awaiting the user.
    pending_question: str = ""
    pending_options: list[Any] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)

    # Confirmed business criteria (口径) injected into SQL, and the questions
    # currently awaiting a reply (paired with the criteria on resume).
    clarifications: list[str] = field(default_factory=list)
    clarify_questions: str = ""

    fail_reason: str = ""
    charts: list[dict[str, Any]] = field(default_factory=list)
    # Every successful execute_sql / execute_readonly_sql in this run (order preserved).
    executed_sqls: list[dict[str, Any]] = field(default_factory=list)
    # The pinned scope (attachments) prioritises the FIRST discovery only; a later
    # discovery in the same run broadens, so a wrong/insufficient pin can't trap the
    # agent into searching only the attached scope forever.
    scope_used: bool = False

    @staticmethod
    def schema_key(database: str, table: str) -> str:
        db = str(database or "").strip()
        name = str(table or "").strip()
        return f"{db}.{name}" if db else name

    @staticmethod
    def schema_table_part(schema_key: str, database: str = "") -> str:
        db = str(database or "").strip()
        key = str(schema_key or "").strip()
        if db and "." in key:
            left, right = key.split(".", 1)
            if left == db:
                return right
        return key

    def note_working_database(self, database: str) -> None:
        """Remember the database currently proven relevant by schema evidence."""
        db = str(database or "").strip()
        if db:
            self.table_database = db

    def remember_table_schema(self, table: str, database: str, columns: list[ColumnInfo]) -> None:
        """Record a described table and keep all schema-related fields coherent."""
        table = str(table or "").strip()
        database = str(database or "").strip()
        if not table:
            return
        key = self.schema_key(database, table)
        self.schemas[key] = list(columns or [])
        self.schema_db[key] = database
        self.table = table
        self.note_working_database(database)
        self.columns = list(columns or [])

    def disclosed_table_names(self) -> list[str]:
        return [
            self.schema_table_part(key, self.schema_db.get(key, ""))
            for key in self.schemas
        ]

    def disclosed_table_keys(self) -> list[tuple[str, str]]:
        keys: list[tuple[str, str]] = []
        for schema_key in self.schemas:
            db = str(self.schema_db.get(schema_key) or self.table_database or self.database or "")
            keys.append((db, self.schema_table_part(schema_key, db)))
        return keys

    def find_schema_columns(self, table: str, database: str = "") -> list[ColumnInfo] | None:
        """Return already-disclosed columns for an unambiguous table reference."""
        table = str(table or "").strip()
        database = str(database or "").strip()
        key = self.schema_key(database, table)
        if key in self.schemas:
            return self.schemas[key]
        matches: list[list[ColumnInfo]] = []
        for schema_key, columns in self.schemas.items():
            schema_db = self.schema_db.get(schema_key, "")
            table_part = self.schema_table_part(schema_key, schema_db)
            database_ok = not database or schema_db == database
            if database_ok and (schema_key == table or table_part == table):
                matches.append(columns)
        if len(matches) == 1:
            return matches[0]
        return None
