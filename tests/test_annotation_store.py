"""Tests for user object annotations (notes on db/table/column)."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.schema_context import (
    apply_column_notes,
    decision_notes_block,
    disclosed_schemas_for_tables,
    object_notes_for_tables,
)
from dbaide.agent.sql_writer import SQLWriter
from dbaide.agent.toolkit import build_tool_registry
from dbaide.annotations import AnnotationStore
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class _MockLLM(LLMClient):
    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def test_upsert_and_scopes(tmp_path):
    store = AnnotationStore(tmp_path / "ann")
    store.add("local", scope="column", note="UTC timestamp; show +8", table="orders", column="paid_at")
    store.add("local", scope="table", note="deprecated, use orders_v2", table="orders")
    store.add("local", scope="database", note="prod; ignore test_* tables", database="shop")
    # Upsert: same object replaces, not appends.
    store.add("local", scope="column", note="UTC; convert to +8 on display", table="orders", column="paid_at")

    records = store.list_records("local")
    assert len(records) == 3
    col = store.list_records("local", scope="column")[0]
    assert "convert to +8" in col["note"]


def test_annotations_for_tables_view(tmp_path):
    store = AnnotationStore(tmp_path / "ann")
    store.add("local", scope="column", note="UTC", database="shop", table="orders", column="paid_at")
    store.add("local", scope="table", note="deprecated", database="shop", table="orders")
    view = store.annotations_for_tables("local", [("shop", "orders")])
    assert view["columns"][("shop", "orders")]["paid_at"] == "UTC"
    assert view["tables"][("shop", "orders")] == "deprecated"


def _orch(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, paid_at INTEGER);")
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    annotations = AnnotationStore(tmp_path / "ann")
    orch = AskOrchestrator(adapter, Session(connection=cfg), _MockLLM(), annotations=annotations)
    return orch, annotations


def test_column_note_backfilled_onto_disclosed_schema(tmp_path):
    orch, annotations = _orch(tmp_path)
    annotations.add("local", scope="column", note="UTC; +8 on display", table="orders", column="paid_at")
    schemas = disclosed_schemas_for_tables(orch, [("", "orders")])
    cols = {c.name: c for _, _, columns in schemas for c in columns}
    assert cols["paid_at"].note == "UTC; +8 on display"


def test_apply_column_notes_on_resolve_path(tmp_path):
    # Simulates resolved.to_disclosed() — a raw list that bypasses
    # _disclosed_schemas_for_tables; notes must still be backfilled.
    orch, annotations = _orch(tmp_path)
    annotations.add("local", scope="column", note="UTC", table="orders", column="paid_at")
    disclosed = [("", "orders", [ColumnInfo(name="paid_at", data_type="int")])]
    apply_column_notes(orch, disclosed)
    assert disclosed[0][2][0].note == "UTC"


def test_object_notes_and_sql_prompt(tmp_path):
    orch, annotations = _orch(tmp_path)
    annotations.add("local", scope="table", note="deprecated, use orders_v2", table="orders")
    notes = object_notes_for_tables(orch, [("", "orders")])
    assert notes and notes[0]["note"] == "deprecated, use orders_v2"

    writer = SQLWriter(_MockLLM(), dialect="sqlite")
    cols = [ColumnInfo(name="paid_at", data_type="int", note="UTC; +8")]
    prompt = writer._user_prompt("how many orders", "orders", cols, {"object_notes": notes})
    assert "AUTHORITATIVE" in prompt
    assert "deprecated, use orders_v2" in prompt
    assert "note(AUTHORITATIVE)=UTC; +8" in prompt


def test_decision_notes_block(tmp_path):
    orch, annotations = _orch(tmp_path)
    annotations.add("local", scope="table", note="弃用，改用 orders_v2", table="orders")
    block = decision_notes_block(orch, "")
    assert "orders" in block
    assert "弃用，改用 orders_v2" in block


def test_apply_notes_to_doc_table(tmp_path):
    from dbaide.annotations import apply_notes_to_doc
    from dbaide.assets.summarizer import render_table_markdown

    store = AnnotationStore(tmp_path / "ann")
    store.add("demo", scope="table", note="deprecated, use orders_v2", database="shop", table="orders")
    store.add("demo", scope="column", note="UTC; +8", database="shop", table="orders", column="paid_at")
    doc = {"kind": "table", "name": "orders", "database": "shop", "columns": [
        {"name": "id", "data_type": "bigint"},
        {"name": "paid_at", "data_type": "bigint"},
    ]}
    apply_notes_to_doc(store, "demo", doc)
    assert doc["user_note"] == "deprecated, use orders_v2"
    assert doc["columns"][1]["user_note"] == "UTC; +8"
    md = render_table_markdown(doc)
    assert "User note" in md and "deprecated, use orders_v2" in md and "UTC; +8" in md


def test_apply_notes_to_doc_database(tmp_path):
    from dbaide.annotations import apply_notes_to_doc

    store = AnnotationStore(tmp_path / "ann")
    store.add("demo", scope="database", note="prod; ignore test_*", database="shop")
    store.add("demo", scope="table", note="legacy", database="shop", table="orders")
    doc = {"kind": "database", "name": "shop", "tables": [
        {"name": "orders"}, {"name": "users"},
    ]}
    apply_notes_to_doc(store, "demo", doc)
    assert doc["user_note"] == "prod; ignore test_*"
    assert doc["tables"][0]["user_note"] == "legacy"
    assert "user_note" not in doc["tables"][1]


class _CapturingLLM(LLMClient):
    """Records the last prompt seen (json + text); returns a fixed reply."""

    def __init__(self, reply: dict | None = None):
        self.reply = reply or {}
        self.last_user = ""
        self.last_prompt = ""

    def complete_json(self, messages, *, schema_hint: str = "") -> dict:
        self.last_user = messages[-1].content
        self.last_prompt = "\n".join(m.content for m in messages)
        return self.reply

    def complete_text(self, messages) -> str:
        self.last_user = messages[-1].content
        self.last_prompt = "\n".join(m.content for m in messages)
        return "ok"


class _FakeOrch:
    def __init__(self, llm, store):
        self.llm = llm
        self.annotations = store
        self.instance = "demo"


def test_schema_linker_sees_table_note(tmp_path):
    # The schema evidence layer must SEE the note and preserve it as excluded
    # evidence for the main LLM; it no longer chooses the replacement itself.
    from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
    from dbaide.agent.schema_link import SchemaEvidenceRetriever

    orch, store = _orch(tmp_path)
    store.add("local", scope="table", note="deprecated; use orders_v2 instead", table="orders")
    orch._discover = lambda q, *, parent="", column_detail=True: DiscoveryResult(
        question=q,
        hits=[SchemaHit(kind="table", path="local.orders", name="orders", table="orders", summary="orders")],
    )
    report = SchemaEvidenceRetriever(orch).retrieve("orders")
    orders = report.candidates[0]
    assert orders.status == "deprecated"
    assert "deprecated; use orders_v2 instead" in orders.exclusion_reason


def test_schema_linker_shows_column_notes(tmp_path):
    from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
    from dbaide.agent.schema_link import SchemaEvidenceRetriever

    orch, store = _orch(tmp_path)
    store.add("local", scope="column", note="UTC; +8 on display", table="orders", column="paid_at")
    orch._discover = lambda q, *, parent="", column_detail=True: DiscoveryResult(
        question=q,
        hits=[SchemaHit(kind="table", path="local.orders", name="orders", table="orders", summary="orders")],
    )
    report = SchemaEvidenceRetriever(orch).retrieve("orders")
    paid_at = next(col for col in report.candidates[0].columns if col["name"] == "paid_at")
    assert paid_at["note"] == "UTC; +8 on display"


def test_attach_notes_to_hits(tmp_path):
    from dbaide.agent.schema_context import attach_notes_to_hits
    from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit

    orch, annotations = _orch(tmp_path)
    annotations.add("local", scope="database", note="prod db", database="shop")
    annotations.add("local", scope="table", note="deprecated", database="shop", table="orders")
    annotations.add("local", scope="column", note="UTC", database="shop", table="orders", column="paid_at")
    discovery = DiscoveryResult(question="q", hits=[
        SchemaHit(kind="database", path="local.shop", name="shop", database="shop"),
        SchemaHit(kind="table", path="local.shop.orders", name="orders", database="shop", table="orders"),
        SchemaHit(kind="column", path="local.shop.orders.paid_at", name="paid_at",
                  database="shop", table="orders"),
    ])
    attach_notes_to_hits(orch, discovery)
    assert discovery.hits[0].note == "prod db"
    assert discovery.hits[1].note == "deprecated"
    assert discovery.hits[2].note == "UTC"


def test_synthesize_answer_sees_notes(tmp_path):
    # The schema answer path must also see
    # notes or it will point the user at the deprecated table.
    from dbaide.agent.progressive_schema import ProgressiveSchemaAgent, DiscoveryResult, SchemaHit

    llm = _CapturingLLM()
    agent = ProgressiveSchemaAgent(llm, None, "demo")
    discovery = DiscoveryResult(question="q", hits=[
        SchemaHit(kind="table", path="demo.data_analysis.product_attributes",
                  name="product_attributes", database="data_analysis",
                  table="product_attributes", summary="product attribute details"),
    ])
    notes = [{"scope": "table", "label": "data_analysis.product_attributes",
              "note": "deprecated; use product_data.product_attributes"}]
    agent.synthesize_answer("产品属性在哪个表？", discovery, object_notes=notes)
    assert "AUTHORITATIVE" in llm.last_prompt
    assert "deprecated; use product_data.product_attributes" in llm.last_prompt


def test_clarifier_sees_object_notes(tmp_path):
    from dbaide.agent.clarify import SemanticClarifier

    llm = _CapturingLLM({"questions": [], "assumptions": []})
    clarifier = SemanticClarifier(llm)
    disclosed = [("data_analysis", "product_attributes",
                  [ColumnInfo(name="design", data_type="varchar")])]
    object_notes = [{"scope": "table", "label": "data_analysis.product_attributes",
                     "note": "deprecated; use product_data.product_attributes"}]
    clarifier.analyze("产品的属性在哪个表能找到？", disclosed, object_notes=object_notes)
    assert "AUTHORITATIVE" in llm.last_user
    assert "deprecated; use product_data.product_attributes" in llm.last_user


def test_annotate_object_tool(tmp_path):
    orch, annotations = _orch(tmp_path)
    registry = build_tool_registry(orch)
    ctx = ToolContext()
    res = registry.invoke(
        "annotate_object",
        {"scope": "column", "note": "UTC", "table": "orders", "column": "paid_at"},
        ctx,
    )
    assert res.ok and res.data["saved"]
    assert annotations.list_records("local", scope="column")[0]["note"] == "UTC"

    # note is required
    bad = registry.invoke("annotate_object", {"scope": "table", "table": "orders"}, ctx)
    assert not bad.ok
