import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent import DataAssistant
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


def test_assistant_progressive_query(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    assistant = DataAssistant(adapter, session, AgentMockLLM())
    response = assistant.ask("最近 7 天每天订单数量", execute=True)
    # Answer language follows the UI setting now (default en); assert behaviour, not language.
    assert response.answer.strip()
    assert any(m in response.answer for m in ("条记录", "查询结果", "rows", "row", "Results"))
    assert response.sql.strip()
    assert response.result is not None
    assert response.result.row_count >= 1
    assert any("L0 instances" in event for event in response.disclosures)
    assert any("L1 databases" in event for event in response.disclosures)
    assert any("L2 tables" in event for event in response.disclosures)
    assert any("L3 columns" in event for event in response.disclosures)
