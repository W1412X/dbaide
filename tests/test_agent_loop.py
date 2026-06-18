import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
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


def test_orchestrator_uses_single_loop(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    response = orch.run("和产线相关的表")
    assert response.answer


def test_orchestrator_returns_honest_failure_no_alternate_pipeline(tmp_path):
    """When the model can't produce a valid decision, the agent surfaces an honest
    failure (with the reason) instead of silently switching execution paths."""
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
    assert "decision_invalid" in blob or "step_budget_exhausted" in blob
    assert "alternate pipeline" not in blob            # no degradation happened
    assert resp.result is None and not resp.sql        # no fabricated staged result


def test_workflow_status_is_failed_when_agent_loop_fails(tmp_path):
    from dbaide.core.result import WorkflowRequest, WorkflowStatus
    from dbaide.core.workflow import WorkflowEngine
    from dbaide.llm import LLMClient

    class _BadLLM(LLMClient):
        def complete_json(self, messages, *, schema_hint=""):
            return {}

        def complete_text(self, messages):
            return ""

    db = tmp_path / "workflow_fail.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    result = WorkflowEngine(conn, llm=_BadLLM()).run(WorkflowRequest(question="订单数量"))

    assert result.status == WorkflowStatus.FAILED
    assert result.warnings
    assert any(event.stage == "workflow_failed" for event in result.trace)


def test_join_relations_require_explicit_tool_call_after_multi_describe(tmp_path):
    db = tmp_path / "multi.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE assets (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE asset_sensors (
            id INTEGER PRIMARY KEY,
            asset_id INTEGER REFERENCES assets(id)
        );
        """
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    session = Session(connection=cfg)
    orch = AskOrchestrator(adapter, session, AgentMockLLM())
    orch._reset_loop_state("sensor query", "", True)
    orch.run_state.schemas = {
        "assets": [ColumnInfo(name="id", data_type="INTEGER", primary_key=True)],
        "asset_sensors": [ColumnInfo(name="asset_id", data_type="INTEGER")],
    }
    orch.run_state.schema_db = {"assets": "", "asset_sensors": ""}
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    assert orch.run_state.relations == []

    result = registry.invoke(
        "retrieve_join_context",
        {"request": "sensor query", "tables": ["assets", "asset_sensors"]},
        ctx,
    )
    assert result.ok
    assert result.data["relations"]
    assert orch.run_state.relations == result.data["relations"]


def test_max_tail_keep_index_keeps_largest_fitting_tail():
    from dbaide.agent.loop import _max_tail_keep_index

    # head=[10,10], overhead=5 → base=25. threshold=100 → tail budget 75.
    # Tail messages are 20 each; 3 fit (60), 4 don't (80). Must keep 3, i.e.
    # first_keep=3 (drop messages[2:3]) — NOT 5 (keeping only the last message).
    sizes = [10, 10, 20, 20, 20, 20]
    assert _max_tail_keep_index(sizes, 100, head=2, overhead=5) == 3

    # When even one tail message can't fit, keep none of the tail (only head+note).
    big = [10, 10, 200, 200]
    assert _max_tail_keep_index(big, 100, head=2, overhead=5) == len(big)

    # When the whole tail fits, keep all of it (first_keep == head).
    small = [10, 10, 5, 5, 5]
    assert _max_tail_keep_index(small, 100, head=2, overhead=5) == 2
