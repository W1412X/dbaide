"""Batched tool calls (action='call_tools') — several INDEPENDENT read-only evidence
tools run in one decision to cut loop round-trips, while anything with a safety gate
or ordering dependency stays one-per-decision."""
import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop, BATCHABLE_TOOLS, _DEFAULT_MAX_BATCH
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session


def _orch(tmp_path, llm):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INT, amount REAL, status TEXT);"
        "CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
        "INSERT INTO orders VALUES (1,1,9.9,'paid'); INSERT INTO users VALUES (1,'A','NYC');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    return AskOrchestrator(build_adapter(conn), Session(connection=conn), llm)


class _BatchLLM(LLMClient):
    """Decision 1: batch two describe_table + one execute_sql. Decision 2: finish."""

    def __init__(self) -> None:
        self.decisions = 0

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        system = messages[0].content if messages else ""
        if "tool loop" not in system.lower():
            return {}
        self.decisions += 1
        if self.decisions == 1:
            return {
                "action": "call_tools",
                "thought": "gather schema for both tables",
                "calls": [
                    {"tool": "describe_table", "args": {"table": "orders"}},
                    {"tool": "describe_table", "args": {"table": "users"}},
                    {"tool": "execute_sql", "args": {"sql": "SELECT 1"}},  # not batchable
                ],
            }
        return {"action": "finish", "answer": "Both tables described."}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def test_batch_runs_independent_reads_and_drops_gated_tools(tmp_path):
    orch = _orch(tmp_path, _BatchLLM())
    loop = AskAgentLoop(orch)

    # Track actual tool invocations via the runtime
    tool_calls: list[str] = []
    from dbaide.agent.runtime import AgentRuntime
    _orig_call = AgentRuntime.call_tool

    def _capture(self, name, args, ctx):
        tool_calls.append(name)
        return _orig_call(self, name, args, ctx)

    AgentRuntime.call_tool = _capture
    try:
        resp = loop.run("describe orders and users", execute=False)
    finally:
        AgentRuntime.call_tool = _orig_call

    assert resp is not None and resp.status == "completed"
    # Both describe_table calls in the one batch ran...
    assert tool_calls.count("describe_table") == 2
    # ...and the gated execute_sql was NOT run from inside the batch.
    assert "execute_sql" not in tool_calls
    # Only ONE decision produced the two reads (the round-trip we saved).
    assert orch.llm.decisions == 2  # the batch decision + the finish decision


def test_batch_calls_filters_and_caps(tmp_path):
    orch = _orch(tmp_path, _BatchLLM())
    loop = AskAgentLoop(orch)
    orch._reset_loop_state("q", "", True)

    runnable, dropped = loop._batch_calls({"calls": [
        {"tool": "describe_table", "args": {"table": "orders"}},
        {"tool": "column_stats", "args": {"table": "orders"}},
        {"tool": "execute_sql", "args": {"sql": "SELECT 1"}},
        {"tool": "ask_user", "args": {"question": "?"}},
        {"tool": "annotate_object", "args": {}},
    ]})
    assert [r["tool"] for r in runnable] == ["describe_table", "column_stats"]
    # Gated/mutating/pausing tools are surfaced, not silently dropped.
    assert set(dropped) == {"execute_sql", "ask_user", "annotate_object"}

    # _DEFAULT_MAX_BATCH cap: extra batchable calls run capped, and the overflow is
    # SURFACED in dropped (not silently lost) so the model re-issues them next round.
    many_runnable, many_dropped = loop._batch_calls({"calls": [
        {"tool": "describe_table", "args": {"table": f"t{i}"}} for i in range(_DEFAULT_MAX_BATCH + 4)
    ]})
    assert len(many_runnable) == _DEFAULT_MAX_BATCH
    assert "describe_table" in many_dropped  # the over-cap calls are not silently dropped


def test_batchable_whitelist_excludes_unsafe_tools():
    # Lock the safety boundary: nothing that executes SQL, pauses, or mutates state.
    for unsafe in ("execute_sql", "generate_sql", "validate_sql",
                   "ask_user", "annotate_object", "add_join", "update_join", "delete_join"):
        assert unsafe not in BATCHABLE_TOOLS
