"""Validation of the post-refactor design:

- There is NO schema-disclosure gate. Table/column existence is proven by the DB
  at execution time, so QueryTools no longer rejects "undisclosed" tables.
- An OPT-IN per-connection table scope (table_allow / table_deny) is the only
  static table gate, and it's stateless.
- 方案② still holds: prior-turn task memory is NOT force-seeded/injected; it is
  preserved into the compressed history.
- The compression hard-truncation backstop still pins the current turn.
"""

from __future__ import annotations

import sqlite3

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools import QueryTools


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE orders(id int); CREATE TABLE customers(id int);")
    c.commit()
    c.close()
    return ConnectionConfig(name="shop", type="sqlite", path=str(db))


@pytest.fixture
def orch(conn):
    return AskOrchestrator(build_adapter(conn), Session(connection=conn))


# ── No disclosure gate: any table validates (existence is the DB's job) ───────

def test_no_gate_unknown_table_validates(conn):
    """Without a configured scope, validation does NOT reject a table just because
    it wasn't 'disclosed' — the DB proves existence at execution time."""
    qt = QueryTools(build_adapter(conn), DisclosureContext())
    report = qt.validate_sql_report("SELECT * FROM whatever_table")
    assert report.ok  # not rejected by any disclosure gate


def test_no_gate_prior_turn_table_usable(conn):
    """A follow-up referencing a table never discovered this turn is not blocked."""
    qt = QueryTools(build_adapter(conn), DisclosureContext())
    assert qt.validate_sql_report("SELECT * FROM orders o JOIN customers c ON o.id=c.id").ok


# ── Opt-in table scope (the only static table gate now) ──────────────────────

def test_scope_allow_enforced(tmp_path):
    db = tmp_path / "s.db"
    sqlite3.connect(db).close()
    conn = ConnectionConfig(name="x", type="sqlite", path=str(db),
                            table_allow=["orders"])
    qt = QueryTools(build_adapter(conn), DisclosureContext())
    assert qt.validate_sql_report("SELECT * FROM orders").ok
    bad = qt.validate_sql_report("SELECT * FROM secret_payroll")
    assert not bad.ok


def test_scope_deny_enforced_even_with_comment(tmp_path):
    db = tmp_path / "s.db"
    sqlite3.connect(db).close()
    conn = ConnectionConfig(name="x", type="sqlite", path=str(db),
                            table_deny=["secret_payroll"])
    qt = QueryTools(build_adapter(conn), DisclosureContext())
    assert qt.validate_sql_report("SELECT * FROM orders").ok
    # comment must not smuggle the denied table past the scope check
    assert not qt.validate_sql_report("SELECT * FROM /*x*/ secret_payroll").ok


def test_scope_config_round_trip(tmp_path):
    from dbaide.config import ConfigManager
    path = tmp_path / "config.toml"
    cfg = ConfigManager(path=path)
    cfg.upsert_connection(
        ConnectionConfig(name="db", type="sqlite", path=str(tmp_path / "d.db"),
                         table_allow=["a", "b"], table_deny=["c"]),
        make_default=True,
    )
    loaded = ConfigManager(path=path).get_connection("db")
    assert loaded.table_allow == ["a", "b"]
    assert loaded.table_deny == ["c"]


# ── 方案②: task memory not force-carried; preserved in compressed history ─────

def test_prior_criteria_and_facts_not_force_seeded(orch):
    orch.active_criteria = ["only paid orders"]
    orch.session_turns = [{
        "status": "completed",
        "clarifications": ["only paid orders"],
        "verified_facts": ["status=2 cancelled"],
        "excluded_paths": [{"target": "orders.legacy_amt", "reason": "deprecated"}],
    }]
    orch._reset_loop_state("how many users?", "", True)
    assert orch.run_state.clarifications == []
    assert orch.run_state.memory.verified_facts == []
    assert orch.run_state.memory.excluded_paths == []


def test_session_turn_prompt_omits_task_memory(orch):
    from dbaide.agent.loop import AskAgentLoop, LoopState
    orch.active_criteria = ["only paid orders"]
    orch.session_turns = [{"status": "completed", "clarifications": ["only paid orders"]}]
    orch._reset_loop_state("how many users?", "", True)
    prompt = AskAgentLoop(orch).prompts.session_turn_prompt(
        LoopState(question="how many users?", database="", execute_allowed=True, answer_language="en"), 2)
    assert "only paid orders" not in prompt
    assert "Confirmed criteria" not in prompt


def test_compression_preserves_criteria_facts_excluded(orch):
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


# ── Compression backstop still pins the current turn ─────────────────────────

def test_hard_truncate_pins_current_turn_start(orch):
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
    assert "[turn:2:start]" in joined and "Confirmed criteria" in joined
