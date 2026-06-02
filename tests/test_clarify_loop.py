"""End-to-end: the loop runs clarify_semantics after resolve_schema; a material
ambiguity pauses (wait_user); on resume the user's answer becomes confirmed
criteria that reach SQL generation."""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig
from dbaide.session import Session


class ClarifyLoopMock(LLMClient):
    """Drives loop → linker → clarifier (asks once) → SQL."""

    def complete_json(self, messages, *, schema_hint=""):
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        if "tool loop" in system.lower():
            order = ["resolve_schema", "clarify_semantics", "generate_sql", "validate_sql", "execute_sql"]
            for name in order:
                if user.count(f"`{name}`") == 0:  # not yet called
                    return {"action": "call_tool", "tool": name, "args": {}}
            return {"action": "finish", "answer": "Done."}
        if "schema linker" in system:
            return {"tables": [{"database": "main", "table": "orders", "columns": ["id", "amount", "status", "created_at"]}],
                    "sufficient": True}
        if "meticulous data analyst" in system:  # the clarifier
            return {"questions": [{"dimension": "time", "ask": "created_at is UTC — which timezone for the window?",
                                   "options": ["UTC", "America/New_York"], "default": "UTC"}],
                    "assumptions": ["Excluding refunded orders"]}
        if "generate safe read-only SQL" in system:
            return {"sql": "SELECT SUM(amount) FROM orders", "rationale": "ok", "confidence": 0.9}
        return {}

    def complete_text(self, messages):
        return "OK"


def _orch(tmp_path):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, amount REAL, status TEXT, created_at TEXT);"
        "INSERT INTO orders VALUES (1, 9.9, 'paid', '2024-01-01');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    store = AssetStore(tmp_path / "assets")
    jc = JoinCatalogStore(base_dir=tmp_path / "joins")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store, join_catalog=jc).build(profile_mode="none", sample=False)
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), ClarifyLoopMock(), asset_store=store, join_catalog=jc)
    orch._discover = lambda q, *, parent="", column_detail=True: DiscoveryResult(
        question=q, hits=[SchemaHit(kind="table", path="shop.main.orders", name="orders", database="main", table="orders", summary="orders")],
    )
    return orch


def test_clarify_pauses_then_criteria_reach_sql(tmp_path):
    orch = _orch(tmp_path)
    captured = {}
    real_write = orch.sql_writer.write

    def spy(question, table="", columns=None, *, disclosed_schemas=None, context=None, feedback=""):
        captured["criteria"] = list((context or {}).get("criteria") or [])
        return real_write(question, table, columns, disclosed_schemas=disclosed_schemas, context=context, feedback=feedback)
    orch.sql_writer.write = spy

    # 1) First run pauses on the timezone ambiguity.
    resp = orch.run("total order amount last month", execute=True)
    assert getattr(resp, "status", "") == "wait_user"
    assert "timezone" in resp.pending_question.lower()
    assert resp.pending_options == ["UTC", "America/New_York"]
    assert "criteria" not in captured  # SQL not generated yet

    # 2) Resume with the user's answer → criteria reach SQL generation.
    resp2 = orch.run("total order amount last month", execute=True,
                     resume_state=resp.resume_state, user_reply="Use America/New_York")
    assert resp2.result is not None and resp2.result.row_count >= 1
    crit = " ".join(captured.get("criteria") or [])
    assert "America/New_York" in crit          # the user's answer is applied
    assert "Excluding refunded orders" in crit  # the clarifier's stated assumption too
