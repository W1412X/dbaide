"""Tests for persistent join catalog."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.schema_context import collect_relations
from dbaide.agent.toolkit import build_tool_registry
from dbaide.connection_identity import connection_fingerprint
from dbaide.joins import JoinCatalogStore, USER_JOIN_CONFIDENCE
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class JoinInferMockLLM(LLMClient):
    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        system = messages[0].content if messages else ""
        if "infer JOIN relationships" in system:
            return {"joins": []}
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def test_user_join_confidence_and_priority(tmp_path):
    store = JoinCatalogStore(tmp_path / "joins")
    store.add(
        "local",
        {
            "table": "orders",
            "column": "user_id",
            "ref_table": "users",
            "ref_column": "id",
            "reason": "manual",
        },
        source="user",
    )
    rels = store.relations_for_tables("local", [("", "orders"), ("", "users")])
    assert len(rels) == 1
    assert rels[0]["confidence"] == USER_JOIN_CONFIDENCE
    assert rels[0]["source"] == "user"


def test_join_catalog_fingerprint_blocks_stale_relations(tmp_path):
    store = JoinCatalogStore(tmp_path / "joins")
    conn1 = ConnectionConfig(name="local", type="sqlite", path=str(tmp_path / "one.db"))
    conn2 = ConnectionConfig(name="local", type="sqlite", path=str(tmp_path / "two.db"))
    fp1 = connection_fingerprint(conn1)
    fp2 = connection_fingerprint(conn2)
    store.add(
        "local",
        {"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"},
        source="user",
        fingerprint=fp1,
    )
    assert len(store.relations_for_tables("local", [("", "orders"), ("", "users")], fingerprint=fp1)) == 1
    assert store.relations_for_tables("local", [("", "orders"), ("", "users")], fingerprint=fp2) == []


def test_join_catalog_is_scoped_by_database_for_same_endpoint(tmp_path):
    store = JoinCatalogStore(tmp_path / "joins")
    rel = {"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"}
    store.add("local", rel, source="user", database="sales")
    store.add("local", {**rel, "reason": "analytics"}, source="user", database="analytics")

    assert len(store.list_records("local")) == 2
    assert len(store.relations_for_tables("local", [("sales", "orders"), ("sales", "users")], database="sales")) == 1
    assert len(store.relations_for_tables("local", [("analytics", "orders"), ("analytics", "users")], database="analytics")) == 1

    assert store.delete("local", endpoint={**rel, "database": "sales"})

    remaining = store.list_records("local")
    assert len(remaining) == 1
    assert remaining[0]["database"] == "analytics"


def test_collect_relations_prefers_user_catalog(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER);
        INSERT INTO users VALUES (1);
        INSERT INTO orders VALUES (1, 1);
        """
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    catalog = JoinCatalogStore(tmp_path / "joins")
    catalog.add(
        "local",
        {"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"},
        source="user",
        fingerprint=connection_fingerprint(cfg),
    )
    orch = AskOrchestrator(adapter, Session(connection=cfg), JoinInferMockLLM(), join_catalog=catalog)
    relations = collect_relations(orch, [("", "orders"), ("", "users")])
    assert relations[0]["source"] == "user"
    assert relations[0]["confidence"] == USER_JOIN_CONFIDENCE


def test_join_tools(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    catalog = JoinCatalogStore(tmp_path / "joins")
    orch = AskOrchestrator(adapter, Session(connection=cfg), JoinInferMockLLM(), join_catalog=catalog)
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    add = registry.invoke(
        "add_join",
        {
            "table": "a",
            "column": "x",
            "ref_table": "b",
            "ref_column": "y",
            "source": "user",
        },
        ctx,
    )
    assert add.ok
    assert add.data["join"]["confidence"] == USER_JOIN_CONFIDENCE
    listed = registry.invoke("list_joins", {"min_confidence": 0.5}, ctx)
    assert listed.ok
    assert listed.data["count"] == 1
    join_id = listed.data["joins"][0]["id"]
    upd = registry.invoke("update_join", {"id": join_id, "reason": "updated note"}, ctx)
    assert upd.ok
    deleted = registry.invoke("delete_join", {"id": join_id}, ctx)
    assert deleted.ok
    assert registry.invoke("list_joins", {}, ctx).data["count"] == 0


def test_persist_agent_candidates(tmp_path):
    store = JoinCatalogStore(tmp_path / "joins")
    saved = store.persist_agent_candidates(
        "local",
        [
            {
                "table": "sensors",
                "column": "asset_id",
                "ref_table": "assets",
                "ref_column": "id",
                "source": "semantic",
                "confidence": 0.72,
            }
        ],
    )
    assert len(saved) == 1
    assert saved[0]["source"] == "agent"
    again = store.persist_agent_candidates(
        "local",
        [
            {
                "table": "sensors",
                "column": "asset_id",
                "ref_table": "assets",
                "ref_column": "id",
                "source": "semantic",
                "confidence": 0.8,
            }
        ],
    )
    assert len(again) == 1
    assert store.list_records("local")[0]["confidence"] == 0.8
