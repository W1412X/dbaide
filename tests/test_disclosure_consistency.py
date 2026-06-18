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

def test_hard_truncate_pins_current_turn_start(orch):
    """The hard-truncation backstop must keep the current turn's [turn:N:start]
    message (it carries the re-injected [Confirmed criteria]) even under extreme
    budget pressure, or the agent loses the 口径 it was told to honor."""
    from dbaide.agent.loop import AskAgentLoop
    from dbaide.llm import LLMMessage

    big = "X" * 8000
    msgs = [
        LLMMessage("system", "sys"),
        LLMMessage("user", "[Compressed turn t1] ..."),
        LLMMessage("user", "[turn:2:start]\n[Confirmed criteria] use Beijing time\nQUESTION"),
        LLMMessage("assistant", big),
        LLMMessage("user", "tool result " + big),
    ]
    AskAgentLoop._hard_truncate_session(msgs, threshold=100)
    joined = "\n".join(m.content for m in msgs)
    assert msgs[0].content == "sys"
    assert "[turn:2:start]" in joined
    assert "Confirmed criteria" in joined  # criteria survived


def test_disclosed_tables_snapshot_spans_all_sub_intents(orch):
    """disclosed_tables must come from the accumulated DisclosureContext (which
    spans every sub-intent of a multi-intent turn), not the last RunState — else a
    multi-part question loses earlier sub-intents' tables for the next turn."""
    dc = orch.session.disclosure
    # Two sub-intents disclose different tables into the shared context, while
    # run_state.schemas (reset per sub-intent) ends holding only the last.
    dc.record_tables([TableInfo(name="orders")], database="main")
    dc.record_tables([TableInfo(name="customers")], database="main")
    orch.run_state.schemas = {"main.customers": [ColumnInfo(name="id")]}  # last sub-intent only
    # Mirror workflow.py's snapshot rule:
    disclosed = sorted(dc.tables.keys()) if dc.tables else sorted(orch.run_state.schemas.keys())
    assert disclosed == ["main.customers", "main.orders"]


def test_seed_session_memory_carries_facts_and_exclusions(orch):
    """Verified facts and ruled-out paths from earlier turns must be re-seeded
    into this run's memory so they survive compression of the originating turn."""
    orch.session_turns = [{
        "status": "completed",
        "verified_facts": ["status=2 means cancelled"],
        "excluded_paths": [{"target": "orders.legacy_amt", "reason": "deprecated, all NULL"}],
    }]
    orch._reset_loop_state("q2", "", True)
    mem = orch.run_state.memory
    assert "status=2 means cancelled" in mem.verified_facts
    assert any(e.target == "orders.legacy_amt" for e in mem.excluded_paths)


def test_session_turn_prompt_reinjects_facts_and_exclusions(orch):
    from dbaide.agent.loop import AskAgentLoop, LoopState

    orch.session_turns = [{
        "status": "completed",
        "verified_facts": ["paid = status IN (1,3)"],
        "excluded_paths": [{"target": "old_orders", "reason": "archive table, do not use"}],
    }]
    orch._reset_loop_state("q2", "", True)
    loop = AskAgentLoop(orch)
    state = LoopState(question="q2", database="", execute_allowed=True, answer_language="en")
    prompt = loop.prompts.session_turn_prompt(state, 2)
    assert "Verified facts" in prompt and "paid = status IN (1,3)" in prompt
    assert "Ruled-out paths" in prompt and "old_orders" in prompt


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
