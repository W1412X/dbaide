"""Unit tests for the single-call LLM SQL optimizer (advisory; reuses the LLM plumbing)."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.optimizer_agent import OptimizerAgent, build_schema_digest, format_explain
from dbaide.context.disclosure import DisclosureContext
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import ConnectionConfig, QueryResult
from dbaide.tools import QueryTools


class _FakeLLM(LLMClient):
    def __init__(self) -> None:
        self.seen = None

    def complete_text(self, messages, *, json_mode=False):  # type: ignore[override]
        self.seen = messages
        return "- add an index on orders.status\n- avoid SELECT *; project only needed columns"


def _qt(tmp_path):
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, status TEXT, amount REAL)")
    con.execute("CREATE INDEX idx_orders_amount ON orders(amount)")
    con.commit()
    con.close()
    return QueryTools(build_adapter(ConnectionConfig(name="a", type="sqlite", path=str(db))),
                      DisclosureContext())


def test_no_model_returns_none(tmp_path):
    agent = OptimizerAgent(NullLLMClient())
    assert agent.evaluate("SELECT * FROM orders", explain_text="x", schema_text="y") is None


def test_calls_model_with_sql_plan_and_schema():
    llm = _FakeLLM()
    out = OptimizerAgent(llm).evaluate(
        "SELECT * FROM orders WHERE status = 'x'",
        explain_text="SCAN orders", schema_text="TABLE orders: id, status", dialect="sqlite")
    assert out and "index" in out.lower()
    user = llm.seen[1].content                      # the user prompt carries all three inputs
    assert "SELECT *" in user and "SCAN orders" in user and "TABLE orders" in user and "sqlite" in user


def test_answer_language_directive_is_applied():
    llm = _FakeLLM()
    OptimizerAgent(llm).evaluate("SELECT * FROM t", explain_text="x", schema_text="y", language="zh")
    system = llm.seen[0].content                    # the system prompt carries the language directive
    assert "简体中文" in system or "Chinese" in system


def test_schema_digest_includes_columns_and_indexes(tmp_path):
    digest = build_schema_digest(_qt(tmp_path).adapter, ["orders"])
    assert "orders" in digest and "status" in digest and "idx_orders_amount" in digest


def test_format_explain_renders_rows():
    qr = QueryResult(columns=["detail"], rows=[["SCAN orders"]], row_count=1, sql="EXPLAIN ...", elapsed_ms=1.0)
    assert "SCAN orders" in format_explain(qr)


def test_evaluate_sql_builds_explain_and_schema_context(tmp_path):
    llm = _FakeLLM()
    out = OptimizerAgent(llm).evaluate_sql("SELECT * FROM orders WHERE status = 'x'", query_tools=_qt(tmp_path))
    assert out is not None
    user = llm.seen[1].content
    assert "orders" in user and "Optimization suggestions:" in user
