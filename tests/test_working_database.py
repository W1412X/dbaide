"""Regression: the database the agent narrows into during exploration must
propagate to the loop's working state, so that when the model omits `database` on
a later tool call, SQL generation/execution target *where the tables were found* —
not the connection's default database.

Reproduces the failure where `list_tables(database='platform')` found `sys_user`,
the model then called describe/execute without a database, and execution fell back
to the connection default (`product_data`) and failed.
"""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.assets import AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import NullLLMClient
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


def _orch(tmp_path):
    db = tmp_path / "x.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE sys_user(user_id INTEGER PRIMARY KEY, del_flag TEXT);")
    c.commit(); c.close()
    conn = ConnectionConfig(name="analysis", type="sqlite", path=str(db))
    orch = AskOrchestrator(
        build_adapter(conn), Session(connection=conn), NullLLMClient(),
        asset_store=AssetStore(tmp_path / "assets"),
        join_catalog=JoinCatalogStore(base_dir=tmp_path / "joins"),
    )
    # db-agnostic stubs: the live catalog returns the same table regardless of which
    # database is asked for (mirrors a multi-db server where the table lives in one).
    orch.schema.list_tables = lambda database="": [type("T", (), {"name": "sys_user", "comment": ""})()]
    orch.schema.describe_table = lambda table, database="": [
        ColumnInfo(name="user_id", data_type="int"), ColumnInfo(name="del_flag", data_type="text"),
    ]
    return orch


def test_list_tables_sets_working_database(tmp_path):
    orch = _orch(tmp_path)
    reg = build_tool_registry(orch)
    orch._reset_loop_state("how many employees", database="", execute=True)  # auto scope
    ctx = ToolContext()
    reg.invoke("list_tables", {"database": "platform"}, ctx)
    assert orch.run_state.table_database == "platform"  # narrowed to the explored db


def test_describe_without_db_inherits_working_database(tmp_path):
    orch = _orch(tmp_path)
    reg = build_tool_registry(orch)
    orch._reset_loop_state("how many employees", database="", execute=True)
    ctx = ToolContext()
    reg.invoke("list_tables", {"database": "platform"}, ctx)
    # model omits the database here — must inherit 'platform', not fall to the default
    res = reg.invoke("describe_table", {"table": "sys_user"}, ctx)
    assert res.ok
    assert res.data["database"] == "platform"
    assert orch.run_state.table_database == "platform"
    assert "platform.sys_user" in orch.run_state.schemas


def test_execute_default_database_is_the_working_db(tmp_path):
    """After exploring 'platform', execute_sql with no explicit database must target
    'platform' (the captured value), not the connection default."""
    orch = _orch(tmp_path)
    reg = build_tool_registry(orch)
    orch._reset_loop_state("how many employees", database="", execute=True)
    ctx = ToolContext()
    reg.invoke("list_tables", {"database": "platform"}, ctx)
    reg.invoke("describe_table", {"table": "sys_user"}, ctx)

    captured = {}

    class _Res:
        columns = ["c"]; rows = [{"c": 1}]; row_count = 1; truncated = False
        sql = "SELECT COUNT(*) FROM sys_user"; elapsed_ms = 1.0

    def _exec(sql, *, database="", limit=100):
        captured["database"] = database
        return _Res()

    orch.query.execute_sql = _exec
    res = reg.invoke("execute_sql", {"sql": "SELECT COUNT(*) FROM sys_user"}, ctx)
    assert res.ok, res.error.to_dict() if res.error else res.data
    assert captured["database"] == "platform"  # not the connection default


def test_explain_default_database_is_the_working_db(tmp_path):
    orch = _orch(tmp_path)
    reg = build_tool_registry(orch)
    orch._reset_loop_state("explain employees", database="", execute=True)
    ctx = ToolContext()
    reg.invoke("list_tables", {"database": "platform"}, ctx)
    reg.invoke("describe_table", {"table": "sys_user"}, ctx)
    captured = {}

    def _diagnose(sql, *, database=""):
        captured["database"] = database
        return {"ok": True, "issues": []}

    orch.diagnose.diagnose_sql = _diagnose
    res = reg.invoke("explain_sql", {"sql": "SELECT COUNT(*) FROM sys_user"}, ctx)

    assert res.ok
    assert captured["database"] == "platform"


def test_empty_db_does_not_clobber_working_db(tmp_path):
    """A later describe with no resolvable db must not reset the working db to ''."""
    orch = _orch(tmp_path)
    reg = build_tool_registry(orch)
    orch._reset_loop_state("q", database="", execute=True)
    ctx = ToolContext()
    reg.invoke("list_tables", {"database": "platform"}, ctx)
    orch.run_state.remember_table_schema("other", "", [ColumnInfo(name="id", data_type="int")])
    assert orch.run_state.table_database == "platform"
