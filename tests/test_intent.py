"""Intent decomposition: Ask splits a question into independent typed sub-intents,
runs each, and aggregates — with every sub-intent's result visible (sections in the
answer + nested, non-colliding trace nodes)."""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.intent import IntentDecomposer
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.agent.trace_model import TraceModel
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import ConnectionConfig
from dbaide.session import Session


# ── decomposer unit ──────────────────────────────────────────────────────────

class _DecompMock(LLMClient):
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, messages, *, schema_hint=""):
        return self.payload

    def complete_text(self, messages):
        return "OK"


def test_decomposer_single_intent_fast_path():
    # No model → exactly one intent (the original question).
    intents = IntentDecomposer(NullLLMClient()).decompose("统计订单数量")
    assert len(intents) == 1 and intents[0].text == "统计订单数量"
    assert intents[0].language == "zh"


def test_decomposer_parses_multiple_typed_intents():
    mock = _DecompMock({"intents": [
        {"type": "schema_explore", "text": "what columns does orders have", "language": "en"},
        {"type": "data_query", "text": "count paid orders", "language": "en"},
        {"type": "bogus", "text": "and avg amount"},  # unknown type → 'other'
    ]})
    intents = IntentDecomposer(mock).decompose("...")
    assert [i.type for i in intents] == ["schema_explore", "data_query", "other"]
    assert intents[1].label == "Data query"
    assert [i.language for i in intents] == ["en", "en", "en"]


def test_decomposer_caps_and_falls_back_on_garbage():
    assert len(IntentDecomposer(_DecompMock({"nope": 1})).decompose("q")) == 1  # fallback


# ── multi-intent end-to-end ──────────────────────────────────────────────────

class MultiMock(LLMClient):
    def complete_json(self, messages, *, schema_hint=""):
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        if system.startswith("You decompose"):
            return {"intents": [
                {"type": "schema_explore", "text": "what columns does orders have"},
                {"type": "data_query", "text": "count paid orders"},
            ]}
        if "tool loop" in system.lower():
            q = user.split("User question:", 1)[1].split("Database scope:", 1)[0].strip() if "User question:" in user else user
            n = user.count("Tool `")
            if "columns" in q:
                return {"action": "finish", "answer": "orders has id, amount, status."} if n else \
                       {"action": "call_tool", "tool": "discover_schema", "args": {"question": q}}
            seq = [
                {"action": "call_tool", "tool": "retrieve_schema_context", "args": {"request": q}},
                {"action": "call_tool", "tool": "generate_sql", "args": {}},
                {"action": "call_tool", "tool": "validate_sql", "args": {}},
                {"action": "call_tool", "tool": "execute_sql", "args": {}},
            ]
            return seq[n] if n < len(seq) else {"action": "finish", "answer": "Counted paid orders."}
        if "generate safe read-only SQL" in system:
            return {"sql": "SELECT COUNT(*) AS n FROM orders WHERE status='paid'", "rationale": "count", "confidence": 0.9}
        return {}

    def complete_text(self, messages):
        return "OK"


def test_run_multi_aggregates_and_nests_trace(tmp_path):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, amount REAL, status TEXT);"
        "INSERT INTO orders VALUES (1,10.0,'paid'),(2,20.0,'pending'),(3,30.0,'paid');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    store = AssetStore(tmp_path / "assets")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store).build(profile_mode="none", sample=False)

    events: list = []
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), MultiMock(),
                           asset_store=store, join_catalog=JoinCatalogStore(base_dir=tmp_path / "joins"),
                           progress=events.append)
    orch._discover = lambda q, *, parent="", column_detail=True, scope=None: DiscoveryResult(
        question=q, hits=[SchemaHit(kind="table", path="shop.main.orders", name="orders",
                                    database="main", table="orders", summary="orders")])

    resp = orch.run("what columns does orders have, and how many paid orders?", execute=True)

    # Aggregated answer has a section per sub-intent.
    assert "## 1." in resp.answer and "## 2." in resp.answer
    assert "orders has id" in resp.answer            # schema sub-intent answer
    assert resp.result is not None                    # data sub-intent kept a concrete result

    # Trace: an intent node per sub-intent, with that intent's steps nested (and no
    # step-id collision across intents).
    model = TraceModel()
    for e in events:
        if isinstance(e, dict):
            model.ingest(e)
    model.finalize()
    assert model.find("intent:i1") is not None and model.find("intent:i2") is not None
    # the data-query intent's execute step nests under that intent's main loop
    assert model.find("intent:i2:loop") is not None and model.find("intent:i2:loop").parent_id == "intent:i2"
    exec_node = model.find("intent:i2:step:4")
    assert exec_node is not None and exec_node.parent_id == "intent:i2:loop"


def test_single_intent_records_decomposition_without_wrapping_loop(tmp_path):
    """A one-thing question still records the decomposition action, but does not
    wrap the main loop in per-intent child nodes."""
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    conn = ConnectionConfig(name="s", type="sqlite", path=str(db))

    class OneMock(MultiMock):
        def complete_json(self, messages, *, schema_hint=""):
            if messages and messages[0].content.startswith("You decompose"):
                return {"intents": [{"type": "schema_explore", "text": "list tables"}]}
            return super().complete_json(messages, schema_hint=schema_hint)

    events: list = []
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), OneMock(),
                           asset_store=AssetStore(tmp_path / "a"), progress=events.append)
    orch._discover = lambda q, *, parent="", column_detail=True, scope=None: DiscoveryResult(question=q, hits=[])
    orch.run("list tables")
    assert any(isinstance(e, dict) and e.get("node_id") == "intent:decompose" for e in events)
    assert not any(
        isinstance(e, dict)
        and str(e.get("node_id", "")).startswith("intent:i")
        for e in events
    )
