import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.sql_writer import SQLWriter
from dbaide.agent.toolkit import build_tool_registry
from dbaide.assets import AssetStore
from dbaide.context.disclosure import DisclosureContext
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.profile import ProfileTools
from dbaide.tools.schema import SchemaTools
from dbaide.tools.registry import ToolContext


class PromptCaptureLLM(LLMClient):
    def __init__(self) -> None:
        self.last_system: str = ""
        self.last_user: str = ""

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        self.last_system = messages[0].content
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


def test_describe_table_returns_table_metadata(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE INDEX idx_orders_user ON orders(user_id)")
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("describe orders", "", True)

    result = registry.invoke("describe_table", {"table": "orders"}, ToolContext())

    assert result.ok
    assert result.data["indexes"]
    assert result.data["foreign_keys"]
    assert result.data["foreign_keys"][0]["ref_table"] == "users"


def test_inspect_metadata_finds_exact_column_name(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE product_sku (id INTEGER PRIMARY KEY, attribute_id INTEGER, sku TEXT)")
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("find attribute columns", "main", True)

    result = registry.invoke(
        "inspect_metadata",
        {"database": "main", "column_name": "attribute_id", "include_columns": True},
        ToolContext(),
    )

    assert result.ok
    assert result.data["matched_columns"]
    assert result.data["matched_columns"][0]["table"] == "product_sku"
    assert result.data["matched_columns"][0]["name"] == "attribute_id"
    assert "product_sku" in result.data["disclosed_tables"]


def test_describe_table_splits_qualified_table_when_database_is_explicit(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("describe orders", "main", True)

    result = registry.invoke("describe_table", {"table": "main.orders", "database": "main"}, ToolContext())

    assert result.ok
    assert result.data["database"] == "main"
    assert result.data["table"] == "orders"
    assert [c["name"] for c in result.data["columns"]]


def test_schema_tools_splits_qualified_table_before_asset_lookup(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    schema = SchemaTools(
        build_adapter(cfg),
        DisclosureContext(),
        instance="local",
        assets=AssetStore(tmp_path / "assets"),
    )

    columns = schema.describe_table("main.orders", database="main")

    assert "total_amount" in {c.name for c in columns}


def test_describe_table_missing_table_is_not_success(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("describe missing", "main", True)

    result = registry.invoke("describe_table", {"table": "missing", "database": "main"}, ToolContext())

    assert not result.ok
    assert result.data == {"table": "missing", "database": "main", "columns": []}


def test_column_stats_splits_qualified_table_when_database_is_explicit(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("stats orders", "main", True)

    result = registry.invoke(
        "column_stats",
        {"table": "main.orders", "database": "main", "columns": ["total_amount"], "metrics": ["min", "max"]},
        ToolContext(),
    )

    assert result.ok
    assert result.data["table"] == "orders"
    assert result.data["columns"][0]["column"] == "total_amount"


def test_profile_tools_split_qualified_table_before_live_stats(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    profile = ProfileTools(
        build_adapter(cfg),
        DisclosureContext(),
        instance="local",
        assets=AssetStore(tmp_path / "assets"),
    )

    stats = profile.column_stats("main.orders", database="main", columns=["total_amount"], metrics=["min", "max"])

    assert stats[0]["column"] == "total_amount"
    assert stats[0]["stats"]["min"] == 10.5


def test_execute_readonly_sql_honors_tool_limit(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?)",
        [(2, 1, 20.0, "2024-01-02"), (3, 1, 30.0, "2024-01-03")],
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), PromptCaptureLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("sample orders", "main", True)

    result = registry.invoke(
        "execute_readonly_sql",
        {"sql": "SELECT id FROM orders ORDER BY id", "database": "main", "limit": 2},
        ToolContext(),
    )

    assert result.ok
    assert result.data["row_count"] == 2
    assert result.data["sql"].endswith("LIMIT 2")



def test_generate_sql_splits_direct_qualified_table_arg(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = PromptCaptureLLM()
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), llm)
    registry = build_tool_registry(orch)
    orch._reset_loop_state("订单数量", "main", True)

    result = registry.invoke(
        "generate_sql",
        {"question": "订单数量", "table": "main.orders", "database": "main"},
        ToolContext(),
    )

    assert result.ok
    assert result.data["tables"] == ["orders"]
    assert "Table: orders" in llm.last_user


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


def test_generate_sql_fails_when_explicit_table_selection_is_incomplete(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = PromptCaptureLLM()
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), llm)
    registry = build_tool_registry(orch)
    orch._reset_loop_state("join orders with missing", "main", True)

    registry.invoke("describe_table", {"table": "orders", "database": "main"}, ToolContext())
    result = registry.invoke(
        "generate_sql",
        {"question": "join orders with missing", "tables": ["orders", "missing_table"], "database": "main"},
        ToolContext(),
    )

    assert not result.ok
    assert result.data["missing_tables"] == ["missing_table"]
    assert "requested table(s) not found" in result.error.message
    assert llm.last_user == ""


def test_generate_sql_rejects_ambiguous_bare_table_selection(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = PromptCaptureLLM()
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), llm)
    registry = build_tool_registry(orch)
    orch._reset_loop_state("orders", "", True)
    orch.run_state.schemas = {
        "sales.orders": [ColumnInfo(name="id", data_type="int")],
        "archive.orders": [ColumnInfo(name="id", data_type="int")],
    }
    orch.run_state.schema_db = {"sales.orders": "sales", "archive.orders": "archive"}

    result = registry.invoke(
        "generate_sql",
        {"question": "orders", "tables": ["orders"]},
        ToolContext(),
    )

    assert not result.ok
    assert result.data["ambiguous_tables"]["orders"] == ["archive.orders", "sales.orders"]
    assert "ambiguous requested table" in result.error.message
    assert llm.last_user == ""


def test_generate_sql_mysql_explicit_database_table_overrides_working_db(tmp_path):
    db = tmp_path / "app.db"
    make_multi_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = PromptCaptureLLM()
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), llm)
    orch.adapter.dialect = "mysql"
    orch.sql_writer.dialect = "mysql"
    registry = build_tool_registry(orch)
    orch._reset_loop_state("orders", "main", True)
    orch.run_state.table_database = "main"
    orch.run_state.schemas = {
        "sales.orders": [ColumnInfo(name="id", data_type="int")],
    }
    orch.run_state.schema_db = {"sales.orders": "sales"}

    result = registry.invoke(
        "generate_sql",
        {"question": "orders", "tables": ["sales.orders"]},
        ToolContext(),
    )

    assert result.ok
    assert result.data["tables"] == ["orders"]
    assert orch.run_state.table_database == "sales"
    assert "main.sales.orders" not in llm.last_user


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


def test_mysql_sql_writer_prompt_includes_version_and_unsupported_syntax_rules():
    llm = PromptCaptureLLM()
    writer = SQLWriter(llm, dialect="mysql", server_version="8.0.36")
    disclosed = [
        ("stats_data", "daily_stats", [ColumnInfo(name="spu", data_type="varchar")]),
        ("order_data", "order_based", [ColumnInfo(name="spu", data_type="varchar")]),
    ]
    writer.write("compare both sides", disclosed_schemas=disclosed, context={})

    assert "Server version: 8.0.36" in llm.last_user
    assert "MySQL does NOT support FULL OUTER JOIN" in llm.last_system
    assert "LEFT JOIN UNION/UNION ALL RIGHT JOIN" in llm.last_system
