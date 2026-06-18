"""Tests for LLM semantic join inference."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.join_inference import (
    SemanticJoinInferencer,
    merge_relation_lists,
    tables_fully_connected,
)
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.schema_context import collect_relations
from dbaide.agent.sql_writer import SQLWriter
from dbaide.agent.toolkit import build_tool_registry
from dbaide.assets import AssetStore
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class JoinInferMockLLM(LLMClient):
    """Returns semantic joins when asked to infer JOIN relationships."""

    last_user: str = ""

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        system = messages[0].content if messages else ""
        self.last_user = messages[-1].content if messages else ""
        if "infer JOIN relationships" in system:
            return {
                "joins": [
                    {
                        "left_table": "asset_sensors",
                        "left_column": "asset_id",
                        "right_table": "assets",
                        "right_column": "id",
                        "confidence": 0.88,
                        "reason": "Each sensor reading belongs to one asset master row.",
                    }
                ]
            }
        return {"sql": "SELECT 1", "rationale": "test", "confidence": 0.8}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def make_unlinked_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE assets (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE asset_sensors (
            id INTEGER PRIMARY KEY,
            asset_id INTEGER,
            reading REAL
        );
        INSERT INTO assets VALUES (1, 'pump'), (2, 'valve');
        INSERT INTO asset_sensors VALUES (10, 1, 1.1), (11, 1, 1.2), (12, 2, 2.1);
        """
    )
    conn.commit()
    conn.close()


def test_tables_fully_connected():
    rels = [{"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"}]
    assert tables_fully_connected(rels, {"orders", "users"})
    assert not tables_fully_connected(rels, {"orders", "users", "products"})
    assert not tables_fully_connected([], {"a", "b"})


def test_semantic_join_inferencer_validates_columns():
    llm = JoinInferMockLLM()
    inferencer = SemanticJoinInferencer(llm, AssetStore(), "local")
    disclosed = [
        ("", "assets", [ColumnInfo(name="id", data_type="INTEGER", primary_key=True)]),
        ("", "asset_sensors", [ColumnInfo(name="asset_id", data_type="INTEGER")]),
    ]
    joins = inferencer.infer("sensors per asset", disclosed)
    assert len(joins) == 1
    assert joins[0]["source"] == "semantic"
    assert joins[0]["ref_table"] == "assets"


def test_validate_joins_allows_self_referential_join():
    # A self-referential FK across DIFFERENT columns (manager_id → id) is a real
    # relationship and must survive; only the degenerate id → id is rejected.
    inferencer = SemanticJoinInferencer(JoinInferMockLLM(), AssetStore(), "local")
    disclosed = [
        ("", "employees", [
            ColumnInfo(name="id", data_type="INTEGER", primary_key=True),
            ColumnInfo(name="manager_id", data_type="INTEGER"),
        ]),
    ]
    raw = [
        {"left_table": "employees", "left_column": "manager_id",
         "right_table": "employees", "right_column": "id", "confidence": 0.9},
        {"left_table": "employees", "left_column": "id",
         "right_table": "employees", "right_column": "id", "confidence": 0.9},
    ]
    out = inferencer._validate_joins(raw, disclosed, [])
    assert len(out) == 1
    assert (out[0]["column"], out[0]["ref_column"]) == ("manager_id", "id")


def test_collect_relations_adds_semantic_when_no_fk(tmp_path):
    db = tmp_path / "unlinked.db"
    make_unlinked_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), JoinInferMockLLM())
    orch._reset_loop_state("sensors without reading", "", True)
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    registry.invoke("describe_table", {"table": "assets"}, ctx)
    registry.invoke("describe_table", {"table": "asset_sensors"}, ctx)
    result = registry.invoke(
        "retrieve_join_context",
        {
            "request": "sensors without reading",
            "tables": ["assets", "asset_sensors"],
            "infer_semantic": True,
            "validate_sample": True,
        },
        ctx,
    )
    assert result.ok
    rels = result.data["relations"]
    assert len([r for r in rels if r.get("source") == "semantic"]) == 1
    assert rels[0]["source"] == "semantic"
    assert rels[0]["column"] == "asset_id"
    assert rels[0].get("validated") is True
    assert rels[0].get("join_type") == "many_to_one"


def test_collect_relations_skips_semantic_when_fk_connects(tmp_path):
    db = tmp_path / "fk.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        INSERT INTO users VALUES (1), (2);
        INSERT INTO orders VALUES (1, 1), (2, 1), (3, 2);
        """
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    orch = AskOrchestrator(adapter, Session(connection=cfg), JoinInferMockLLM())
    relations = collect_relations(orch, [("", "orders"), ("", "users")], validate_sample=True)
    assert len(relations) == 1
    assert relations[0]["source"] == "foreign_key"
    assert relations[0].get("validated") is True
    assert relations[0].get("join_type") == "many_to_one"


def test_merge_relation_lists_prefers_declared():
    declared = [{"table": "a", "column": "x", "ref_table": "b", "ref_column": "id", "source": "foreign_key"}]
    semantic = [{"table": "a", "column": "x", "ref_table": "b", "ref_column": "id", "source": "semantic"}]
    merged = merge_relation_lists(declared, semantic)
    assert len(merged) == 1
    assert merged[0]["source"] == "foreign_key"


def test_sql_writer_formats_semantic_joins():
    llm = JoinInferMockLLM()
    writer = SQLWriter(llm, dialect="sqlite")
    ctx = {
        "foreign_keys": [
            {
                "table": "asset_sensors",
                "column": "asset_id",
                "ref_table": "assets",
                "ref_column": "id",
                "source": "semantic",
                "confidence": 0.9,
                "reason": "sensor belongs to asset",
            }
        ]
    }
    writer.write("q", "asset_sensors", [ColumnInfo(name="asset_id", data_type="INTEGER")], context=ctx)
    assert "Semantic join hints" in llm.last_user
    assert "conf=90%" in llm.last_user


def test_semantic_join_prompt_includes_authoritative_column_notes(tmp_path):
    llm = JoinInferMockLLM()
    inf = SemanticJoinInferencer(llm, AssetStore(tmp_path / "assets"), "local")

    inf.infer(
        "join orders to users",
        [
            ("main", "orders", [ColumnInfo(name="user_code", data_type="TEXT", note="business user key")]),
            ("main", "users", [ColumnInfo(name="code", data_type="TEXT")]),
        ],
        declared=[],
    )

    assert "user_note(AUTHORITATIVE)=business user key" in llm.last_user


def test_join_evidence_counts_cross_db_same_name_tables_as_two(monkeypatch):
    """Two same-named tables in different databases are two real join targets — the
    table-count gate must count (db, table) pairs, not bare names, so a cross-DB
    reconciliation join isn't rejected as 'insufficient tables'."""
    from dbaide.agent import join_evidence as je

    # Stub the heavy collection so only the table-count gate is exercised.
    monkeypatch.setattr(je, "disclosed_schemas_for_tables", lambda orch, targets: [])
    monkeypatch.setattr(je, "collect_relations", lambda *a, **k: [{"source": "foreign_key"}])

    class _Mem:
        join_reports: list = []
        def add_join_report(self, _r): pass

    class _RS:
        trace_node = ""
        table_database = ""
        database = ""
        question = "reconcile orders across dbs"
        relations: list = []
        memory = _Mem()
        def disclosed_table_keys(self): return []

    class _Adapter:
        dialect = "postgres"

    class _Orch:
        run_state = _RS()
        adapter = _Adapter()
        def progress(self, _ev): pass

    retriever = je.JoinEvidenceRetriever(_Orch())
    report = retriever.retrieve("reconcile", tables=["db1.orders", "db2.orders"], database="")

    assert len(report.tables) == 2
    assert not any("at least two tables" in w for w in report.warnings)
    assert report.relations == [{"source": "foreign_key"}]
