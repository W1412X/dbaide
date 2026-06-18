"""Tests for join type classification and sample validation."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from dbaide.adapters import build_adapter
from dbaide.agent.join_validation import (
    JoinSampleValidator,
    classify_join_type,
    type_alignment_score,
)
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext
from dbaide.models import QueryResult


class JoinInferMockLLM(LLMClient):
    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        system = messages[0].content if messages else ""
        if "infer JOIN relationships" in system:
            return {
                "joins": [
                    {
                        "left_table": "asset_sensors",
                        "left_column": "asset_id",
                        "right_table": "assets",
                        "right_column": "id",
                        "confidence": 0.9,
                        "reason": "sensor belongs to asset",
                    }
                ]
            }
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def seed_asset_sensor_db(path):
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


def test_type_alignment_score():
    assert type_alignment_score("INTEGER", "BIGINT") >= 0.85
    assert type_alignment_score("VARCHAR(50)", "TEXT") >= 0.85
    assert type_alignment_score("BLOB", "INTEGER") < 0.5
    assert type_alignment_score("BLOB", "INTEGER") > 0.0


def test_classify_join_type():
    assert classify_join_type(max_right_per_left=1, max_left_per_right=1) == "one_to_one"
    assert classify_join_type(max_right_per_left=3, max_left_per_right=1) == "one_to_many"
    assert classify_join_type(max_right_per_left=1, max_left_per_right=4) == "many_to_one"
    assert classify_join_type(max_right_per_left=2, max_left_per_right=3) == "many_to_many"
    # No observed join pairs → no cardinality evidence → "unknown", NOT a false 1:1.
    assert classify_join_type(max_right_per_left=0, max_left_per_right=0) == "unknown"
    assert classify_join_type(max_right_per_left=0, max_left_per_right=5) == "unknown"
    assert classify_join_type(max_right_per_left=5, max_left_per_right=0) == "unknown"


def test_sample_validation_many_to_one(tmp_path):
    db = tmp_path / "linked.db"
    seed_asset_sensor_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), JoinInferMockLLM())
    rel = {
        "table": "asset_sensors",
        "column": "asset_id",
        "ref_table": "assets",
        "ref_column": "id",
        "source": "semantic",
    }
    validator = JoinSampleValidator(orch, sample_size=50)
    out = validator.validate_one(rel, col_types={
        ("assets", "id"): "INTEGER",
        ("asset_sensors", "asset_id"): "INTEGER",
    }, table_db={"assets": "", "asset_sensors": ""})
    assert out["validated"] is True
    assert out["join_type"] == "many_to_one"
    assert out["validation"]["match_rate"] >= 0.45


def test_validate_joins_tool(tmp_path):
    db = tmp_path / "linked.db"
    seed_asset_sensor_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), JoinInferMockLLM())
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    orch._reset_loop_state("sensor stats", "", True)
    registry.invoke("describe_table", {"table": "assets"}, ctx)
    registry.invoke("describe_table", {"table": "asset_sensors"}, ctx)
    rel_result = registry.invoke(
        "retrieve_join_context",
        {
            "request": "sensor stats",
            "tables": ["assets", "asset_sensors"],
            "infer_semantic": True,
            "validate_sample": True,
        },
        ctx,
    )
    assert rel_result.ok
    assert len(rel_result.data["relations"]) >= 1
    rels = rel_result.data["relations"]
    assert rels[0].get("join_type") in {"many_to_one", "one_to_many", "one_to_one"}
    revalidate = registry.invoke("validate_joins", {"sample_size": 80}, ctx)
    assert revalidate.ok
    assert revalidate.data["validated_count"] >= 1


def test_incompatible_types_keep_low_confidence(tmp_path):
    db = tmp_path / "badtypes.db"
    sqlite3.connect(db).close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), JoinInferMockLLM())
    rel = {
        "table": "a",
        "column": "blob_col",
        "ref_table": "b",
        "ref_column": "id",
        "source": "semantic",
        "confidence": 0.7,
    }
    validator = JoinSampleValidator(orch)
    out = validator.validate_one(
        rel,
        col_types={("a", "blob_col"): "BLOB", ("b", "id"): "INTEGER"},
    )
    assert float(out.get("confidence") or 0) < float(rel["confidence"])
    assert out["validation"]["type_alignment"] < 0.5


def test_join_sql_has_no_correlated_subquery(tmp_path):
    """The max-cardinality probes must use GROUP BY joins, not correlated subqueries."""
    import re
    from dbaide.observability import query_log

    db = tmp_path / "linked.db"
    seed_asset_sensor_db(db)
    conn = ConnectionConfig(name="joinsql", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    orch = AskOrchestrator(adapter, Session(connection=conn), JoinInferMockLLM())
    validator = JoinSampleValidator(orch, sample_size=50)
    stats = validator._sample_stats("asset_sensors", "asset_id", "assets", "id", database="")
    assert stats["max_left_per_right"] >= 2  # asset 1 has two sensors

    sqls = [e.sql.lower() for e in query_log.for_instance("joinsql").recent()]
    # No "(SELECT COUNT(*) FROM <t> ... WHERE ... = ...)" correlated pattern.
    for sql in sqls:
        assert not re.search(r"\(\s*select\s+count\(\*\)\s+from\s+\w+\s+\w+\s+where", sql)


def test_join_sample_sql_keeps_each_side_database():
    recorded: list[tuple[str, str]] = []

    class FakeQuery:
        def execute_sql(self, sql, *, database="", limit=10):
            recorded.append((database, sql))
            if "COUNT(DISTINCT l.v)" in sql:
                return QueryResult(columns=["sampled", "matched"], rows=[{"sampled": 1, "matched": 1}], row_count=1)
            return QueryResult(columns=["max_cnt"], rows=[{"max_cnt": 1}], row_count=1)

    orch = SimpleNamespace(
        adapter=SimpleNamespace(dialect="mysql"),
        query=FakeQuery(),
        run_state=SimpleNamespace(table_database="", database=""),
    )
    validator = JoinSampleValidator(orch, sample_size=50, dialect="mysql")
    stats = validator._sample_stats(
        "orders",
        "sku",
        "refunds",
        "sku",
        database="",
        left_database="order_data",
        right_database="stats_data",
    )

    assert stats["match_rate"] == 1
    assert recorded
    assert all(database == "order_data" for database, _ in recorded)
    assert all("`order_data`.`orders`" in sql for _, sql in recorded)
    assert all("`stats_data`.`refunds`" in sql for _, sql in recorded)
