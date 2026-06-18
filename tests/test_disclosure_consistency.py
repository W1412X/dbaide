"""Agent consistency: the SchemaGuard disclosure gate must stay in sync with
what the agent already knows — across turns, across pause/resume, and regardless
of message compression (which only rewrites the LLM stream, never the gate)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ConnectionConfig, TableInfo
from dbaide.session import Session
from dbaide.validation import SchemaGuard


# ── DisclosureContext re-disclosure primitives ───────────────────────────────

def test_ensure_table_idempotent():
    dc = DisclosureContext()
    assert dc.ensure_table("main", "orders") is True
    assert dc.ensure_table("main", "orders") is False  # already present
    assert "main.orders" in dc.tables


def test_ensure_table_sets_columns_once():
    dc = DisclosureContext()
    assert dc.ensure_table("main", "t", [ColumnInfo(name="a")]) is True
    # already disclosed with columns → no change
    assert dc.ensure_table("main", "t", [ColumnInfo(name="b")]) is False
    assert {c.name for c in dc.tables["main.t"].columns} == {"a"}


def test_redisclose_emits_one_summary_event_and_counts():
    dc = DisclosureContext()
    n = dc.redisclose([("main", "orders"), ("main", "customers"), ("", "logs")])
    assert n == 3
    events = [e for e in dc.events if "re-disclosed" in e]
    assert len(events) == 1
    # re-running adds nothing
    assert dc.redisclose([("main", "orders")]) == 0


# ── Cross-turn: prior-turn tables remain usable ──────────────────────────────

@pytest.fixture
def orch(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE orders(id int); CREATE TABLE customers(id int);")
    c.commit()
    c.close()
    cfg = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    session = Session(connection=cfg)
    return AskOrchestrator(build_adapter(cfg), session)


def test_seed_session_disclosure_carries_prior_turn_tables(orch):
    # Turn 1 disclosed orders; this (fresh) turn must still recognize it.
    orch.session_turns = [{"disclosed_tables": ["orders"], "status": "completed"}]
    orch._seed_session_disclosure()
    r = SchemaGuard().validate("SELECT * FROM orders", orch.session.disclosure)
    assert r.ok, [i.message for i in r.issues]


def test_seed_then_new_discovery_both_usable(orch):
    orch.session_turns = [{"disclosed_tables": ["orders"], "status": "completed"}]
    orch._seed_session_disclosure()
    # This turn additionally discovers customers.
    orch.session.disclosure.record_tables([TableInfo(name="customers")], database="")
    r = SchemaGuard().validate(
        "SELECT * FROM orders o JOIN customers c ON o.id=c.id", orch.session.disclosure,
    )
    assert r.ok


def test_seed_no_prior_turns_is_noop(orch):
    orch.session_turns = []
    orch._seed_session_disclosure()
    assert orch.session.disclosure.tables == {}


def test_undisclosed_table_still_blocked_after_seed(orch):
    orch.session_turns = [{"disclosed_tables": ["orders"], "status": "completed"}]
    orch._seed_session_disclosure()
    r = SchemaGuard().validate("SELECT * FROM secret_payroll", orch.session.disclosure)
    assert not r.ok  # carrying prior tables must NOT open the gate to everything


# ── Resume: restore_loop_state re-discloses the in-flight discovery ───────────

def test_restore_loop_state_rediscloses_schemas(orch):
    from dbaide.agent.loop_state import restore_loop_state

    # A paused run had discovered orders(id) — captured in run_state.schemas.
    snapshot = {
        "version": 4,
        "question": "q",
        "database": "",
        "execute_allowed": True,
        "messages": [],
        "run_state": {
            "schemas": {"orders": [{"name": "id", "data_type": "int"}]},
            "schema_db": {"orders": ""},
        },
    }
    # Fresh disclosure (as on resume) — empty before restore.
    assert orch.session.disclosure.tables == {}
    restore_loop_state(orch, snapshot)
    # The gate now recognizes the previously-discovered table.
    r = SchemaGuard().validate("SELECT id FROM orders", orch.session.disclosure)
    assert r.ok, [i.message for i in r.issues]


# ── Compression independence ─────────────────────────────────────────────────

def test_disclosure_is_independent_of_message_compression(orch):
    """Compression rewrites the LLM message stream only; the disclosure gate is
    runtime state on session.disclosure and must be unaffected, so SQL on an
    already-disclosed table keeps validating after compression."""
    from dbaide.agent.loop import AskAgentLoop
    from dbaide.llm import LLMMessage

    orch.session.disclosure.record_tables([TableInfo(name="orders")], database="")
    # Build an oversized session stream and force a hard truncation.
    big = "X" * 6000
    msgs = [LLMMessage("system", "sys")]
    for n in (1, 2):
        msgs.append(LLMMessage("user", f"[turn:{n}:start]\n{big}"))
        msgs.append(LLMMessage("assistant", big))
        msgs.append(LLMMessage("user", f"[turn:{n}:end] done"))
    AskAgentLoop._hard_truncate_session(msgs, threshold=100)  # aggressively drop history

    # Gate still recognizes orders — disclosure was never touched by compression.
    r = SchemaGuard().validate("SELECT * FROM orders", orch.session.disclosure)
    assert r.ok
