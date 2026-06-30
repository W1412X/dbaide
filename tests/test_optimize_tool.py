"""SQL optimizer: a proactive optimize_sql tool (advice) + an automatic rewrite-and-sync on
heavy queries (the agent's SQL is replaced with the optimized version)."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import LOOP_DECISION_TOOL_NAMES, build_tool_registry
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class _AdviceLLM(LLMClient):
    def complete_text(self, messages: list[LLMMessage], *, json_mode=False):  # type: ignore[override]
        return "- add an index on orders.status (the filter column)"


class _RewriteLLM(LLMClient):
    def complete_text(self, messages, *, json_mode=False):  # type: ignore[override]
        return "advice"

    def complete_json(self, messages, *, schema_hint=""):  # type: ignore[override]
        return {"rewritten_sql": "SELECT id, status FROM orders WHERE status = 'x'",
                "rationale": "filter on the indexed status column; select only needed columns"}


class _NoRewriteLLM(LLMClient):
    def complete_json(self, messages, *, schema_hint=""):  # type: ignore[override]
        return {"rewritten_sql": "", "rationale": "add an index on orders.status"}


def _orch(tmp_path, llm=None):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
    con.commit(); con.close()
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), llm or _AdviceLLM())
    orch._reset_loop_state("show orders", "", True)
    orch.query.optimize_advise_rows = 1_000_000
    return orch


def test_optimize_sql_is_an_available_tool():
    assert "optimize_sql" in LOOP_DECISION_TOOL_NAMES


def test_optimize_sql_tool_returns_suggestions(tmp_path):
    registry = build_tool_registry(_orch(tmp_path))   # tool = advice (complete_text)
    r = registry.invoke("optimize_sql", {"sql": "SELECT * FROM orders WHERE status = 'x'"}, ToolContext())
    assert r.ok and "index" in r.data["suggestions"].lower()


def test_auto_rewrite_adopts_cheaper_query_and_syncs_agent_sql(tmp_path):
    orch = _orch(tmp_path, llm=_RewriteLLM())
    # original heavy; the rewrite (projects id, status) is cheap → adopted
    orch.query.estimate_rows = lambda sql, database="": 500_000 if "id, status" in sql else 2_000_000
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert r.ok and "columns" in r.data
    assert "id" in r.data["sql"] and "status" in r.data["sql"]   # executed the optimized query
    assert "SELECT *" in r.data["optimized_from"]                 # discloses the original
    assert r.data.get("optimization_rationale")
    assert orch.run_state.sql == r.data["sql"]                    # the agent's SQL is synced to X'


def test_no_rewrite_falls_back_to_advice_and_runs_original(tmp_path):
    orch = _orch(tmp_path, llm=_NoRewriteLLM())
    orch.query.estimate_rows = lambda sql, database="": 2_000_000
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert "columns" in r.data and "SELECT *" in r.data["sql"]    # original ran
    assert "optimized_from" not in r.data
    assert "index" in r.data.get("optimization", "").lower()      # rationale attached as advice


def test_no_optimization_for_a_light_query(tmp_path):
    orch = _orch(tmp_path, llm=_RewriteLLM())
    orch.query.estimate_rows = lambda sql, database="": 100       # under threshold
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert "optimization" not in r.data and "optimized_from" not in r.data
