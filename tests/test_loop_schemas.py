import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.sql_writer import SQLWriter
from dbaide.agent.toolkit import build_tool_registry
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class PromptCaptureLLM(LLMClient):
    def __init__(self) -> None:
        self.last_user: str = ""

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        self.last_user = messages[-1].content
        return {"sql": "SELECT 1", "rationale": "test", "confidence": 0.8}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def make_multi_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            email TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total_amount REAL,
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        INSERT INTO users VALUES (1, 'Alice', 'a@example.com');
        INSERT INTO orders VALUES (1, 1, 10.5, DATE('now'));
        """
    )
    conn.commit()
    conn.close()


def test_describe_table_accumulates_schemas(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, PromptCaptureLLM())
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("multi", "", True)

    r1 = registry.invoke("describe_table", {"table": "orders"}, ctx)
    r2 = registry.invoke("describe_table", {"table": "users"}, ctx)

    assert r1.ok and r2.ok
    assert len(orch.run_state.schemas) == 2
    assert "orders" in r2.data["disclosed_tables"]
    assert "users" in r2.data["disclosed_tables"]
    assert orch.run_state.columns == orch.run_state.schemas["users"]


def test_generate_sql_uses_all_disclosed_schemas(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    llm = PromptCaptureLLM()
    session = Session(connection=conn)
    orch = AskOrchestrator(adapter, session, llm)
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("每个用户的订单数", "", True)

    registry.invoke("describe_table", {"table": "orders"}, ctx)
    registry.invoke("describe_table", {"table": "users"}, ctx)
    result = registry.invoke(
        "generate_sql",
        {"question": "每个用户的订单数"},
        ctx,
    )

    assert result.ok
    assert set(result.data["tables"]) == {"orders", "users"}
    assert "Disclosed schemas" in llm.last_user
    assert "Table: orders" in llm.last_user
    assert "Table: users" in llm.last_user
    assert "user_id" in llm.last_user


def test_sql_writer_single_table_unchanged():
    llm = PromptCaptureLLM()
    writer = SQLWriter(llm, dialect="sqlite")
    columns = [ColumnInfo(name="id", data_type="INTEGER", primary_key=True)]
    writer.write("count rows", "orders", columns, context={})
    assert "Table: orders" in llm.last_user
    assert "Disclosed schemas" not in llm.last_user


def test_sql_writer_multi_table_prompt():
    llm = PromptCaptureLLM()
    writer = SQLWriter(llm, dialect="sqlite")
    disclosed = [
        ("", "orders", [ColumnInfo(name="user_id", data_type="INTEGER")]),
        ("", "users", [ColumnInfo(name="name", data_type="TEXT")]),
    ]
    writer.write("join query", disclosed_schemas=disclosed, context={})
    assert "Disclosed schemas" in llm.last_user
    assert "Table: orders" in llm.last_user
    assert "Table: users" in llm.last_user
