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


def test_prior_facts_not_force_seeded_into_memory(orch):
    """方案②: prior-turn verified facts / excluded paths are NOT force-loaded into
    a fresh run's memory (that, plus prompt re-injection, contaminated unrelated
    follow-ups). They live in history via compression; the model uses them by
    relevance. A fresh turn's memory starts clean."""
    orch.session_turns = [{
        "status": "completed",
        "verified_facts": ["status=2 means cancelled"],
        "excluded_paths": [{"target": "orders.legacy_amt", "reason": "deprecated, all NULL"}],
    }]
    orch._reset_loop_state("q2", "", True)
    mem = orch.run_state.memory
    assert mem.verified_facts == []
    assert mem.excluded_paths == []


def test_session_turn_prompt_does_not_inject_task_memory(orch):
    """方案②: an unrelated follow-up's prompt must NOT carry prior criteria/facts/
    excluded as authoritative — no contamination."""
    from dbaide.agent.loop import AskAgentLoop, LoopState

    orch.session_turns = [{
        "status": "completed",
        "clarifications": ["only paid orders"],
        "verified_facts": ["paid = status IN (1,3)"],
        "excluded_paths": [{"target": "old_orders", "reason": "archive table"}],
    }]
    orch.active_criteria = ["only paid orders"]
    orch._reset_loop_state("how many users?", "", True)
    loop = AskAgentLoop(orch)
    state = LoopState(question="how many users?", database="", execute_allowed=True, answer_language="en")
    prompt = loop.prompts.session_turn_prompt(state, 2)
    assert "only paid orders" not in prompt
    assert "paid = status IN (1,3)" not in prompt
    assert "old_orders" not in prompt
    assert "Confirmed criteria" not in prompt


def test_compression_preserves_criteria_facts_excluded(orch):
    """方案② enabler: the per-turn compression summary preserves criteria, verified
    facts and excluded paths into history (so the model can attend to them by
    relevance on later turns without force-injection)."""
    from dbaide.agent.loop import AskAgentLoop
    from dbaide.llm import LLMMessage

    orch.session_turns = [{
        "status": "completed", "question": "orders analysis",
        "disclosed_tables": ["main.orders"],
        "clarifications": ["only paid orders"],
        "verified_facts": ["status=2 = cancelled"],
        "excluded_paths": [{"target": "orders.legacy_amt", "reason": "all NULL"}],
        "answer_markdown": "100 orders",
    }]
    summary = AskAgentLoop(orch)._fallback_turn_summary(
        orch, [LLMMessage("user", "[turn:1:start]\norders analysis")], 1)
    assert "only paid orders" in summary
    assert "status=2 = cancelled" in summary
    assert "orders.legacy_amt" in summary


def test_prior_disclosed_keys_reads_last_completed_turn_only(orch):
    """P3: the most recent completed turn's disclosed_tables is the cumulative
    session set, so seeding reads only it (O(tables), not O(turns*tables))."""
    orch.session_turns = [
        {"status": "completed", "disclosed_tables": ["main.orders"]},
        {"status": "completed", "disclosed_tables": ["main.orders", "main.customers"]},
    ]
    keys = orch._prior_disclosed_keys()
    assert sorted(keys) == [("main", "customers"), ("main", "orders")]


def test_rehydrate_run_state_schemas_from_assets(tmp_path):
    """P1: columns for earlier-turn tables are rehydrated into run_state.schemas
    from the OFFLINE asset cache (no DB round-trip), so generate_sql finds them
    via find_schema_columns instead of re-describing."""
    from dbaide.assets import AssetBuilder, AssetStore

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE orders(id INTEGER PRIMARY KEY, total REAL, status TEXT);")
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    store = AssetStore(tmp_path / "assets")
    AssetBuilder(connection=cfg, adapter=adapter, store=store).build(profile_mode="none")

    orch = AskOrchestrator(adapter, Session(connection=cfg), asset_store=store)
    orch.session_turns = [{"status": "completed", "disclosed_tables": ["main.orders"]}]
    orch._reset_loop_state("q2", "", True)  # triggers rehydrate

    cols = orch.run_state.find_schema_columns("orders", "main")
    assert cols is not None and {c.name for c in cols} == {"id", "total", "status"}


def test_known_tables_line_in_prompt(orch):
    """P2: the disclosure gate is echoed into the turn prompt so the model knows
    which prior tables it may query directly."""
    from dbaide.agent.loop import AskAgentLoop, LoopState

    orch.session_turns = [{"status": "completed", "disclosed_tables": ["main.orders"]}]
    orch._seed_session_disclosure()
    orch._reset_loop_state("q2", "", True)
    loop = AskAgentLoop(orch)
    state = LoopState(question="q2", database="", execute_allowed=True, answer_language="en")
    prompt = loop.prompts.session_turn_prompt(state, 2)
    assert "Tables already available this session" in prompt and "main.orders" in prompt


def test_sql_writer_context_filtered_to_targets(orch):
    """P5: the SQL-writer schema context is scoped to the generate_sql targets,
    not every table carried across the session."""
    from dbaide.models import ColumnInfo as _Col
    dc = orch.session.disclosure
    # Many carried tables in the gate, only one is the target.
    for n in range(10):
        dc.record_tables([TableInfo(name=f"carried{n}")], database="main")
    dc.record_tables([TableInfo(name="orders")], database="main")
    dc.record_columns("orders", [_Col(name="id")], database="main")

    disclosed = [("main", "orders", [_Col(name="id")])]
    target_refs = {(str(db or ""), str(t)) for db, t, _ in disclosed}
    summary = dc.summary()
    summary["tables"] = [
        t for t in summary.get("tables", [])
        if (str(t.get("database") or ""), str(t.get("name") or "")) in target_refs
    ]
    assert [t["name"] for t in summary["tables"]] == ["orders"]  # carried* filtered out


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
