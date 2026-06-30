"""The SQL optimizer soft-gate: advise a heavy query once (before executing), then the
agent's next execute_sql runs straight through — never a re-advise loop."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
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
    orch.query.optimize_advise_mode = "gate"
    orch.query.optimize_advise_rows = 1_000_000
    orch.query.estimate_rows = lambda sql, database="": 2_000_000   # force "heavy"
    return orch


def test_gate_advises_once_then_resubmission_runs(tmp_path):
    orch = _orch(tmp_path)
    registry = build_tool_registry(orch)

    # 1st call: gated — advice returned, NOT executed, the one-shot flag is armed
    r1 = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert r1.ok
    assert r1.data.get("executed") is False
    assert "index" in r1.data["optimization"].lower()
    assert orch.run_state.skip_next_optimize is True

    # 2nd call (the agent's resubmission — same or rewritten): runs, no re-advise, flag cleared
    r2 = registry.invoke("execute_sql", {"sql": "SELECT id, status FROM orders"}, ToolContext())
    assert r2.ok
    assert "columns" in r2.data and r2.data.get("executed") is not False   # actually executed
    assert "optimization" not in r2.data                                   # not re-advised
    assert orch.run_state.skip_next_optimize is False


def test_suggest_mode_executes_and_attaches_advice(tmp_path):
    orch = _orch(tmp_path)
    orch.query.optimize_advise_mode = "suggest"      # old behavior: run, then advise
    registry = build_tool_registry(orch)
    r = registry.invoke("execute_sql", {"sql": "SELECT * FROM orders"}, ToolContext())
    assert r.ok
    assert "columns" in r.data                        # executed
    assert "index" in r.data["optimization"].lower()  # advice attached to the result
    assert orch.run_state.skip_next_optimize is False  # no gate, no flag
