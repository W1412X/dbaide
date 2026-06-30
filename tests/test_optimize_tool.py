"""Option A: the optimizer is an agent-invoked tool (optimize_sql). The agent calls it when
it wants advice; execute_sql just hints toward it for heavy queries — no gate, no tracking."""

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


def _orch(tmp_path):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
    con.commit(); con.close()
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), _AdviceLLM())
    orch._reset_loop_state("show orders", "", True)
    orch.query.optimize_advise_rows = 1_000_000
    return orch


def test_optimize_sql_is_an_available_tool():
    assert "optimize_sql" in LOOP_DECISION_TOOL_NAMES   # the model can choose to call it


def test_optimize_sql_tool_returns_suggestions(tmp_path):
    registry = build_tool_registry(_orch(tmp_path))
    r = registry.invoke("optimize_sql", {"sql": "SELECT * FROM orders WHERE status = 'x'"}, ToolContext())
    assert r.ok
    assert "index" in r.data["suggestions"].lower()     # advice, never executes/rewrites


def test_execute_sql_hints_the_tool_when_heavy_but_still_runs(tmp_path):
    orch = _orch(tmp_path)
    orch.query.estimate_rows = lambda sql, database="": 2_000_000   # heavy
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert r.ok and "columns" in r.data                  # executed — no gate
    assert "optimize_sql" in r.data.get("optimization_hint", "")   # nudges toward the tool


def test_execute_sql_no_hint_for_a_light_query(tmp_path):
    orch = _orch(tmp_path)
    orch.query.estimate_rows = lambda sql, database="": 100
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert "optimization_hint" not in r.data
