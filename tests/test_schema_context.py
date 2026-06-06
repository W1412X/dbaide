import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.agent.schema_context import (
    collect_relations,
    merge_sql_context,
    table_targets_from_discovery,
    table_targets_from_hits,
    validation_feedback,
)
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


def make_fk_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total_amount REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    conn.close()


def test_table_targets_from_hits_dedupes_and_limits():
    hits = [
        SchemaHit(kind="table", name="orders", table="orders", database="main", path="", summary=""),
        SchemaHit(kind="table", name="orders", table="orders", database="main", path="", summary=""),
        SchemaHit(kind="table", name="users", table="users", database="main", path="", summary=""),
        SchemaHit(kind="column", name="user_id", table="orders", database="main", path="", summary=""),
    ]
    targets = table_targets_from_hits(hits, "main", limit=4)
    assert targets == [("main", "orders"), ("main", "users")]


def test_table_targets_from_discovery():
    discovery = DiscoveryResult(
        question="orders and users",
        hits=[
            SchemaHit(kind="table", name="orders", table="orders", database="", path="", summary=""),
            SchemaHit(kind="table", name="users", table="users", database="", path="", summary=""),
        ],
    )
    assert table_targets_from_discovery(discovery, "app") == [("app", "orders"), ("app", "users")]


def test_collect_relations_from_live_catalog(tmp_path):
    db = tmp_path / "fk.db"
    make_fk_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), PromptCaptureLLM())
    relations = collect_relations(orch, [("", "orders"), ("", "users")])
    assert len(relations) == 1
    assert relations[0]["column"] == "user_id"
    assert relations[0]["ref_table"] == "users"


def test_retrieve_join_context_tool(tmp_path):
    db = tmp_path / "fk.db"
    make_fk_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("join", "", True)
    registry.invoke("describe_table", {"table": "orders"}, ctx)
    registry.invoke("describe_table", {"table": "users"}, ctx)
    result = registry.invoke(
        "retrieve_join_context",
        {"request": "join", "tables": ["orders", "users"]},
        ctx,
    )
    assert result.ok
    assert len(result.data["relations"]) == 1
    assert orch.run_state.relations[0]["ref_table"] == "users"


def test_generate_sql_prompt_includes_foreign_keys(tmp_path):
    db = tmp_path / "fk.db"
    make_fk_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    llm = PromptCaptureLLM()
    orch = AskOrchestrator(adapter, Session(connection=conn), llm)
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("count per user", "", True)
    registry.invoke("describe_table", {"table": "orders"}, ctx)
    registry.invoke("describe_table", {"table": "users"}, ctx)
    registry.invoke(
        "retrieve_join_context",
        {"request": "orders per user", "tables": ["orders", "users"]},
        ctx,
    )
    registry.invoke("generate_sql", {"question": "orders per user"}, ctx)
    assert "Declared foreign keys" in llm.last_user
    assert "orders.user_id -> users.id" in llm.last_user


def test_merge_sql_context_and_writer_format():
    ctx = merge_sql_context({"disclosed": ["orders"]}, [{"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"}])
    llm = PromptCaptureLLM()
    writer = SQLWriter(llm, dialect="sqlite")
    writer.write("q", "orders", [ColumnInfo(name="user_id", data_type="INTEGER")], context=ctx)
    assert "Declared foreign keys" in llm.last_user
    assert "foreign_keys" not in llm.last_user


def test_validation_feedback_hints_missing_schema():
    msg = validation_feedback(["Unknown column: orders.missing_col"])
    assert "describe_table" in msg
