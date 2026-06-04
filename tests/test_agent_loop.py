import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop, LoopState, ToolCallRecord
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.runtime import AgentRuntime
from dbaide.agent.toolkit import build_tool_registry
from dbaide.core.result import ExecutionPolicy
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext
from tests.llm_mock import AgentMockLLM


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total_amount REAL,
            created_at TEXT
        );
        INSERT INTO orders VALUES (1, 1, 10.5, DATE('now', '-1 day'));
        """
    )
    conn.commit()
    conn.close()


def test_toolkit_discover_schema(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("orders table", "", True)
    result = registry.invoke("discover_schema", {"question": "orders table"}, ToolContext())
    assert result.ok
    assert "hits" in (result.data or {})


def test_agent_loop_schema_question(tmp_path):
    db = tmp_path / "lines.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE production_lines (line_id INTEGER PRIMARY KEY, line_name TEXT);"
        "INSERT INTO production_lines VALUES (1, 'A');"
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    session = Session(connection=cfg)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    response = AskAgentLoop(orch).run("和产线相关的表")
    assert response is not None
    assert "产线" in response.answer or "production" in response.answer.lower()


def test_agent_loop_data_query(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    response = AskAgentLoop(orch).run("最近 7 天每天订单数量", execute=True)
    assert response is not None
    assert response.result is not None or "sql" in response.answer.lower()


def test_orchestrator_uses_loop_before_staged(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    response = orch.run("和产线相关的表")
    assert response.answer


def test_orchestrator_returns_honest_failure_no_staged_degrade(tmp_path):
    """When the model can't produce a valid decision, the agent surfaces an honest
    failure (with the reason) — it must NOT silently degrade to a staged pipeline."""
    from dbaide.llm import LLMClient

    class _BadLLM(LLMClient):
        def complete_json(self, messages, *, schema_hint=""):
            return {}  # never a valid action (and intent-decompose falls back to single)

        def complete_text(self, messages):
            return ""

    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), _BadLLM())
    resp = orch.run("和产线相关的表")
    blob = " ".join(resp.warnings)
    assert "decision_invalid" in blob                  # the real reason is surfaced
    assert "staged pipeline" not in blob               # no degradation happened
    assert resp.result is None and not resp.sql        # no fabricated staged result


def test_auto_get_relations_after_multi_describe(tmp_path):
    db = tmp_path / "multi.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE assets (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE asset_sensors (id INTEGER PRIMARY KEY, asset_id INTEGER);
        """
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    session = Session(connection=cfg)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    orch._reset_loop_state("sensor query", "", True)
    orch._loop_schemas = {
        "assets": [ColumnInfo(name="id", data_type="INTEGER", primary_key=True)],
        "asset_sensors": [ColumnInfo(name="asset_id", data_type="INTEGER")],
    }
    orch._loop_schema_db = {"assets": "", "asset_sensors": ""}
    loop = AskAgentLoop(orch)
    state = LoopState(question="sensor query", database="", execute_allowed=True)
    registry = build_tool_registry(orch)
    runtime = AgentRuntime(
        llm=orch.llm,
        tool_registry=registry,
        execution_policy=ExecutionPolicy.SAFE_AUTO,
    )
    ctx = ToolContext()
    first = loop._auto_get_relations_if_needed(state, runtime, ctx)
    assert first is not None
    assert first.ok
    state.calls.append(ToolCallRecord(tool="get_relations", args={}, ok=True, summary="ok"))
    assert loop._auto_get_relations_if_needed(state, runtime, ctx) is None
