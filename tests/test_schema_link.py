"""Schema Linker: resolves a minimal-necessary schema (less noise → better SQL),
validates picks against the real catalog, accumulates monotonically, and can pause
to ask the user. Scenarios are simulated with a scripted selection mock; the
discovery step is stubbed so we test the linker's selection/validation logic."""

import sqlite3

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.agent.schema_link import SchemaLinker
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import ConnectionConfig
from dbaide.session import Session


class SelectMock(LLMClient):
    """Drives only the linker's selection call; returns `selection` (dict or fn)."""

    def __init__(self, selection):
        self.selection = selection

    def complete_json(self, messages, *, schema_hint=""):
        system = messages[0].content if messages else ""
        if "schema linker" in system:
            sel = self.selection
            return sel(messages[-1].content) if callable(sel) else sel
        return {}

    def complete_text(self, messages):
        return "OK"


def _orch(tmp_path, llm, *, hits):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INT REFERENCES users(id), amount REAL, status TEXT, created_at TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, sku TEXT, price REAL);"
        "CREATE TABLE shipments(id INTEGER PRIMARY KEY, order_id INT, carrier TEXT);"
        "CREATE TABLE returns(id INTEGER PRIMARY KEY, order_id INT, reason TEXT);"
        "INSERT INTO users VALUES (1,'A','NYC'); INSERT INTO orders VALUES (1,1,9.9,'paid','2024-01-01');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    store = AssetStore(tmp_path / "assets")
    jc = JoinCatalogStore(base_dir=tmp_path / "joins")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store, join_catalog=jc).build(profile_mode="none", sample=False)
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), llm, asset_store=store, join_catalog=jc)
    orch._discover = lambda q, *, parent="", column_detail=True: DiscoveryResult(  # stub discovery
        question=q,
        hits=[SchemaHit(kind="table", path=f"shop.main.{t}", name=t, database="main", table=t, summary=f"{t} table") for t in hits],
    )
    return orch


def test_single_table_minimal(tmp_path):
    orch = _orch(tmp_path, SelectMock({
        "tables": [{"database": "main", "table": "orders", "columns": ["id", "amount", "status"]}],
        "sufficient": True,
    }), hits=["orders", "users", "items", "shipments", "returns"])
    r = SchemaLinker(orch).resolve("total paid order amount")
    assert [t["table"] for t in r.tables] == ["orders"]            # only the needed table
    assert {c.name for c in r.tables[0]["columns"]} == {"id", "amount", "status"}  # only needed columns


def test_many_tables_pick_one_is_noise_reduction(tmp_path):
    orch = _orch(tmp_path, SelectMock({
        "tables": [{"database": "main", "table": "items", "columns": ["sku", "price"]}],
        "sufficient": True,
    }), hits=["orders", "users", "items", "shipments", "returns"])
    r = SchemaLinker(orch).resolve("list product skus")
    assert len(r.tables) == 1 and r.tables[0]["table"] == "items"  # 1 of 5 candidates


def test_multi_table_join_resolved(tmp_path):
    orch = _orch(tmp_path, SelectMock({
        "tables": [
            {"database": "main", "table": "orders", "columns": ["id", "user_id", "amount"]},
            {"database": "main", "table": "users", "columns": ["id", "name"]},
        ],
        "sufficient": True,
    }), hits=["orders", "users"])
    r = SchemaLinker(orch).resolve("each user's total order amount")
    assert {t["table"] for t in r.tables} == {"orders", "users"}
    assert any(j.get("ref_table") == "users" or j.get("table") == "orders" for j in r.joins)  # FK join mapped


def test_ambiguous_asks_user(tmp_path):
    orch = _orch(tmp_path, SelectMock({
        "tables": [], "sufficient": False,
        "ask": {"question": "Which 'amount' — order amount or item price?", "options": ["orders.amount", "items.price"]},
    }), hits=["orders", "items"])
    r = SchemaLinker(orch).resolve("total amount")
    assert r.pending_question.startswith("Which 'amount'")
    assert r.pending_options == ["orders.amount", "items.price"]
    assert not r.sufficient


def test_existence_validation_drops_unknown_column(tmp_path):
    orch = _orch(tmp_path, SelectMock({
        "tables": [{"database": "main", "table": "orders", "columns": ["amount", "ghost_col"]}],
        "sufficient": True,
    }), hits=["orders"])
    r = SchemaLinker(orch).resolve("order amounts")
    names = {c.name for c in r.tables[0]["columns"]}
    assert "amount" in names and "ghost_col" not in names   # nonexistent column dropped
    assert "ghost_col" in r.notes


def test_no_llm_falls_back_to_all_discovered(tmp_path):
    orch = _orch(tmp_path, NullLLMClient(), hits=["orders", "users"])
    r = SchemaLinker(orch).resolve("anything")
    assert {t["table"] for t in r.tables} == {"orders", "users"}  # deterministic fallback


class LoopMock(LLMClient):
    """Drives the whole loop end-to-end: loop decision → linker selection → SQL."""

    def complete_json(self, messages, *, schema_hint=""):
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        if "tool loop" in system.lower():
            n = user.count("Tool `")
            steps = [
                {"action": "call_tool", "tool": "resolve_schema", "args": {"question": "paid order amounts"}},
                {"action": "call_tool", "tool": "generate_sql", "args": {}},
                {"action": "call_tool", "tool": "validate_sql", "args": {}},
                {"action": "call_tool", "tool": "execute_sql", "args": {}},
            ]
            return steps[n] if n < len(steps) else {"action": "finish", "answer": "Done."}
        if "schema linker" in system:
            return {"tables": [{"database": "main", "table": "orders", "columns": ["id", "amount", "status"]}],
                    "sufficient": True}
        if "generate safe read-only SQL" in system:
            return {"sql": "SELECT SUM(amount) AS total FROM orders WHERE status='paid'",
                    "rationale": "sum paid", "confidence": 0.9}
        return {}

    def complete_text(self, messages):
        return "OK"


def test_end_to_end_loop_uses_minimal_resolved_schema(tmp_path):
    orch = _orch(tmp_path, LoopMock(), hits=["orders", "users", "items", "shipments", "returns"])
    captured = {}
    real_write = orch.sql_writer.write

    def spy(question, table="", columns=None, *, disclosed_schemas=None, context=None, feedback=""):
        cols = columns if columns is not None else (disclosed_schemas[0][2] if disclosed_schemas else [])
        captured["columns"] = [c.name for c in cols]
        captured["tables"] = [table] if table else [t for _, t, _ in (disclosed_schemas or [])]
        return real_write(question, table, columns, disclosed_schemas=disclosed_schemas, context=context, feedback=feedback)

    orch.sql_writer.write = spy
    resp = orch.run("total paid order amounts", execute=True)

    # The loop resolved a minimal schema (only orders, only the 3 picked columns)…
    assert [t["table"] for t in orch._loop_resolved_schema.tables] == ["orders"]
    # …and generate_sql was handed exactly that minimal column set (not all 5 of orders).
    assert captured["tables"] == ["orders"]
    assert set(captured["columns"]) == {"id", "amount", "status"}
    assert resp.result is not None and resp.result.row_count >= 1


def test_linker_discovers_tables_only_not_full_cascade(tmp_path):
    """Efficiency: the linker gets the big direction (relevant tables) without the
    per-column LLM cascade — that detail is confirmed in its single _select call."""
    captured = {}
    orch = _orch(tmp_path, SelectMock({
        "tables": [{"database": "main", "table": "orders", "columns": ["id"]}], "sufficient": True,
    }), hits=["orders"])
    base = orch._discover

    def spy(q, *, parent="", column_detail=True):
        captured["column_detail"] = column_detail
        return base(q, parent=parent, column_detail=column_detail)

    orch._discover = spy
    SchemaLinker(orch).resolve("order ids")
    assert captured["column_detail"] is False  # tables-only discovery


class _FilterSelectMock(LLMClient):
    """Drives discovery's relevance filter (keep all) + the linker's selection."""
    def complete_json(self, messages, *, schema_hint=""):
        import re
        sysmsg = messages[0].content if messages else ""
        if "relevant_indices" in sysmsg:   # discovery shortlist/filter step
            return {"relevant_indices": [int(m) for m in re.findall(r"\[(\d+)\]", messages[-1].content)]}
        if "schema linker" in sysmsg:
            return {"tables": [
                {"database": "main", "table": "orders", "columns": ["id", "user_id", "amount"]},
                {"database": "main", "table": "users", "columns": ["id", "name"]},
            ], "sufficient": True}
        return {}
    def complete_text(self, messages):
        return "OK"


def test_trace_is_a_true_call_tree(tmp_path):
    """resolve_schema → Schema discovery → (its filters); resolve_schema → Map
    relations → join validation. Each callee nests under its caller."""
    from dbaide.agent.trace_model import TraceModel
    orch = _orch(tmp_path, _FilterSelectMock(), hits=["orders", "users"])
    del orch._discover  # use the REAL progressive discovery (so its internals emit)
    events: list = []
    orch.progress = events.append
    orch._loop_trace_node = "step:1"  # simulate the loop assigning the tool node
    SchemaLinker(orch).resolve("total amount per user")

    m = TraceModel()
    m.ingest({"stage": "resolve_schema", "title": "resolve_schema", "status": "running", "kind": "tool", "step": 1})
    for e in events:
        if isinstance(e, dict):
            m.ingest(e)
    m.finalize()

    discover = m.find("step:1/discover_1")
    relations = m.find("step:1/relations")
    assert discover is not None and discover.parent_id == "step:1"      # discovery under resolve
    assert relations is not None and relations.parent_id == "step:1"    # relations under resolve
    # discovery's own steps nest under the discovery node (not under resolve)
    assert discover.children and all(ch.parent_id == discover.id for ch in discover.children)
    assert any("database" in ch.title for ch in discover.children)
    # the join validation nests under "Map relations"
    assert relations.children and any(ch.agent == "join_validate" for ch in relations.children)


def test_normalize_db_table_splits_qualified_name():
    from dbaide.agent.schema_context import normalize_db_table
    assert normalize_db_table("platform.sys_user", "") == ("platform", "sys_user")
    assert normalize_db_table("sys_user", "platform") == ("platform", "sys_user")
    assert normalize_db_table("`platform`.`sys_user`", "") == ("platform", "sys_user")
    assert normalize_db_table("sys_user", "") == ("", "sys_user")
