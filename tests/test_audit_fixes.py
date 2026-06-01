"""Regression tests for the end-to-end audit fixes:

1. Self-correction returns the corrected SQL/rationale so the answer never shows
   the failed original SQL alongside a result produced by different SQL.
2. explain_sql surfaces its diagnosis as the loop answer instead of dropping it.
3. record_columns registers the table even without a prior list_tables so the
   SchemaGuard hallucination check stays fail-closed.
"""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AgentContext, AskOrchestrator
from dbaide.agent.sql_writer import SQLDraft
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


# ── 1. self-correction carries the corrected SQL/rationale ──────────────────

def test_self_correction_returns_corrected_sql_and_draft(tmp_path):
    orch = _orchestrator(tmp_path)
    corrected = SQLDraft(sql="SELECT SUM(amount) AS total FROM orders", rationale="fixed", confidence=0.9)
    orch.sql_writer.write = lambda *a, **k: corrected  # type: ignore[assignment]

    ctx = AgentContext(question="total amount", table="orders", error="no such function: no_such_func")
    result = orch._attempt_self_correction(ctx, orch.schema.describe_table("orders"), "")

    assert result is not None
    res, sql, draft = result  # the caller unpacks exactly this 3-tuple
    assert "SUM(amount)" in sql
    assert draft.rationale == "fixed"
    assert res.row_count == 1


# ── 2. explain_sql does not drop its diagnosis ──────────────────────────────

def test_explain_sql_sets_loop_answer(tmp_path):
    from dbaide.agent.toolkit import build_tool_registry

    orch = _orchestrator(tmp_path)
    orch._loop_answer = ""
    registry = build_tool_registry(orch)
    handler = registry._handlers["explain_sql"]  # _ctx is unused by _explain_sql
    result = handler({"sql": "SELECT amount FROM orders"}, None)

    assert result.ok
    assert "EXPLAIN diagnosis" in orch._loop_answer


# ── 3. record_columns is fail-closed without a prior list_tables ────────────

def test_record_columns_registers_table_without_prior_disclosure():
    ctx = DisclosureContext()
    cols = [ColumnInfo(name="id", data_type="INTEGER"), ColumnInfo(name="amount", data_type="REAL")]
    ctx.record_columns("orders", cols, database="main")

    assert "orders" in ctx.table_names()
    known = ctx.known_columns()
    assert "orders" in known and {"id", "amount"} <= known["orders"]
