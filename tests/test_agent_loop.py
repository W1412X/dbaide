import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext, ToolResult
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


def test_non_session_compression_uses_reported_prompt_tokens(tmp_path):
    from dbaide.llm import LLMMessage

    db = tmp_path / "usage.db"
    make_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = AgentMockLLM()
    llm.last_usage = {"prompt_tokens": 7000}
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), llm)
    orch.model_config = type("_SmallContext", (), {"context_length": 8000})()
    orch.session.compress_threshold = 50
    loop = AskAgentLoop(orch)
    messages = [
        LLMMessage("system", "sys"),
        LLMMessage("user", "question"),
        LLMMessage("assistant", '{"action":"call_tool","tool":"list_tables","args":{}}'),
        LLMMessage("user", "[Tool result: list_tables]\nsmall"),
        LLMMessage("assistant", '{"action":"call_tool","tool":"describe_table","args":{"table":"orders"}}'),
        LLMMessage("user", "[Tool result: describe_table]\nsmall"),
        LLMMessage("assistant", '{"action":"call_tool","tool":"validate_sql","args":{"sql":"SELECT 1"}}'),
        LLMMessage("user", "[Tool result: validate_sql]\nok"),
        LLMMessage("assistant", '{"action":"finish","answer":"done"}'),
        LLMMessage("user", "tail"),
    ]

    loop._maybe_compress(orch, messages)

    assert any("Context summary" in m.content or "earlier messages were dropped" in m.content for m in messages)


def test_tool_result_formatter_uses_configurable_char_limit():
    from dbaide.agent.loop import _format_tool_result

    result = ToolResult(ok=True, data={"payload": "x" * 200})
    text = _format_tool_result("unknown_tool", result, char_limit=80)

    assert len(text) < 140
    assert "truncated from" in text


def test_tool_result_formatter_allows_unlimited_char_limit():
    from dbaide.agent.loop import _format_tool_result

    result = ToolResult(ok=True, data={"payload": "x" * 200})
    text = _format_tool_result("unknown_tool", result, char_limit=0)

    assert "truncated from" not in text
    assert "x" * 200 in text


def test_tool_result_formatter_defaults_to_unlimited_char_limit():
    from dbaide.agent.loop import _format_tool_result

    result = ToolResult(ok=True, data={"payload": "x" * 200})
    text = _format_tool_result("unknown_tool", result)

    assert "truncated from" not in text
    assert "x" * 200 in text


def test_loop_reads_latest_result_limit_from_session(tmp_path):
    db = tmp_path / "limit.db"
    make_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    session = Session(connection=cfg)
    session.latest_result_limit = 0
    orch = AskOrchestrator(build_adapter(cfg), session, AgentMockLLM())

    assert AskAgentLoop._tool_result_char_limit(orch) is None
    session.latest_result_limit = 1234
    assert AskAgentLoop._tool_result_char_limit(orch) == 1234


def test_latest_result_limit_defaults_to_unlimited():
    from dbaide.db.policy import ResourcePolicy

    cfg = ConnectionConfig(name="local", type="sqlite", path=":memory:")
    assert ResourcePolicy().latest_result_limit == 0
    assert Session(connection=cfg).latest_result_limit == 0
    assert Session.from_policy(cfg, ResourcePolicy()).latest_result_limit == 0


def test_run_single_propagates_cancellation(tmp_path):
    """User cancellation must NOT be swallowed by the loop's generic exception handler:
    CancelledError (which subclasses Exception) must propagate so the workflow maps it
    to a CANCELLED status instead of an 'agent loop failed' answer."""
    from dbaide.core.cancellation import CancelledError

    db = tmp_path / "c.db"
    make_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    orch = AskOrchestrator(adapter, Session(connection=cfg), AgentMockLLM())

    def _boom() -> None:
        raise CancelledError("user cancelled")

    orch.cancel_check = _boom
    import pytest
    with pytest.raises(CancelledError):
        orch.run("how many orders", database="", execute=True)
