"""Regression tests for the end-to-end audit fixes:

1. explain_sql surfaces its diagnosis as the loop answer instead of dropping it.
2. record_columns registers the table even without a prior list_tables so the
   SchemaGuard hallucination check stays fail-closed.
"""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from tests.llm_mock import AgentMockLLM


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL);
        INSERT INTO orders VALUES (1, 2.0), (2, 3.0);
        """
    )
    conn.commit()
    conn.close()


def _orchestrator(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    session = Session(connection=cfg)
    return AskOrchestrator(build_adapter(cfg), session, AgentMockLLM())


# ── 1. explain_sql does not drop its diagnosis ──────────────────────────────

def test_explain_sql_sets_loop_answer(tmp_path):
    from dbaide.agent.toolkit import build_tool_registry

    orch = _orchestrator(tmp_path)
    orch._loop_answer = ""
    registry = build_tool_registry(orch)
    handler = registry._handlers["explain_sql"]  # _ctx is unused by _explain_sql
    result = handler({"sql": "SELECT amount FROM orders"}, None)

    assert result.ok
    assert "EXPLAIN diagnosis" in orch._loop_answer


# ── 2. record_columns is fail-closed without a prior list_tables ────────────

def test_record_columns_registers_table_without_prior_disclosure():
    ctx = DisclosureContext()
    cols = [ColumnInfo(name="id", data_type="INTEGER"), ColumnInfo(name="amount", data_type="REAL")]
    ctx.record_columns("orders", cols, database="main")

    assert "orders" in ctx.table_names()
    known = ctx.known_columns()
    assert "orders" in known and {"id", "amount"} <= known["orders"]
