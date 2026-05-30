import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.models import ConnectionConfig
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
