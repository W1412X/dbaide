import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.controllers import RiskController
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.core.result import ExecutionPolicy, ValidationReport
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from tests.llm_mock import AgentMockLLM


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total_amount REAL,
            status TEXT,
            created_at TEXT
        );
        INSERT INTO orders VALUES
            (1, 1, 10.5, 'paid', DATE('now', '-1 day')),
            (2, 1, 20.0, 'pending', DATE('now', '-2 day')),
            (3, 2, 30.0, 'paid', DATE('now', '-3 day'));
        """
    )
    conn.commit()
    conn.close()


def test_orchestrator_live_schema_query_without_assets(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orchestrator = AskOrchestrator(adapter, session, AgentMockLLM())
    response = orchestrator.run("最近 7 天每天订单数量", execute=True)
    # Answer language follows the UI setting now (default en); assert behaviour, not language.
    assert response.answer.strip()
    assert any(m in response.answer for m in ("条记录", "查询结果", "rows", "row", "Results"))
    assert response.sql.strip()
    assert response.result is not None
    assert response.result.row_count >= 1


def test_risk_controller_blocks_sql_only_policy():
    risk = RiskController()
    decision = risk.decide(
        policy=ExecutionPolicy.SQL_ONLY,
        validation=ValidationReport(ok=True, normalized_sql="SELECT 1", issues=[]),
        plan_confidence=0.9,
    )
    assert decision.action == "generate_only"


def test_orchestrator_schema_explore_without_assets(tmp_path):
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
    orchestrator = AskOrchestrator(adapter, session, AgentMockLLM())
    response = orchestrator.run("和产线相关的表")
    assert "production" in response.answer.lower() or "产线" in response.answer
