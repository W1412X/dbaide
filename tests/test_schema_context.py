import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.agent.schema_context import (
    collect_relations,
    merge_sql_context,
    normalize_db_table_for_dialect,
    table_targets_from_discovery,
    table_targets_from_hits,
    validation_feedback,
)
from dbaide.agent.join_validation import JoinSampleValidator
from dbaide.agent.sql_writer import SQLWriter
from dbaide.agent.toolkit import build_tool_registry
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ColumnInfo, ConnectionConfig, QueryResult
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


def test_normalize_db_table_for_dialect_respects_mysql_database_prefix():
    assert normalize_db_table_for_dialect("sales.orders", "main", "mysql") == ("sales", "orders")
    assert normalize_db_table_for_dialect("sales.orders", "main", "postgres") == ("main", "sales.orders")


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


def test_join_sample_validation_uses_working_database(tmp_path, monkeypatch):
    db = tmp_path / "fk.db"
    make_fk_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), PromptCaptureLLM())
    orch._reset_loop_state("join", "other", True)
    orch.run_state.table_database = "main"
    seen_databases: list[str] = []

    def fake_execute(sql, *, database="", limit=10):
        seen_databases.append(database)
        if "matched" in sql:
            return QueryResult(["sampled", "matched"], [{"sampled": 1, "matched": 1}], 1)
        return QueryResult(["max_cnt"], [{"max_cnt": 1}], 1)

    monkeypatch.setattr(orch.query, "execute_sql", fake_execute)

    validator = JoinSampleValidator(orch, sample_size=20)
    relation = validator.validate_one(
        {"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id", "source": "semantic"},
        col_types={("orders", "user_id"): "INTEGER", ("users", "id"): "INTEGER"},
        table_db={},
    )

    assert relation["validated"]
    assert seen_databases and set(seen_databases) == {"main"}


def test_join_catalog_tools_normalize_qualified_endpoints(tmp_path):
    db = tmp_path / "fk.db"
    make_fk_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(
        build_adapter(conn),
        Session(connection=conn),
        PromptCaptureLLM(),
        join_catalog=JoinCatalogStore(base_dir=tmp_path / "joins"),
    )
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("join", "main", True)

    add = registry.invoke(
        "add_join",
        {
            "database": "main",
            "table": "main.orders",
            "column": "user_id",
            "ref_table": "main.users",
            "ref_column": "id",
        },
        ctx,
    )
    listed = registry.invoke("list_joins", {"tables": ["main.orders"], "database": "main"}, ctx)

    assert add.ok
    assert add.data["join"]["database"] == "main"
    assert add.data["join"]["table"] == "orders"
    assert add.data["join"]["ref_table"] == "users"
    assert listed.ok
    assert listed.data["count"] == 1
    assert listed.data["joins"][0]["table"] == "orders"


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
