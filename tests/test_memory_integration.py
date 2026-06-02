"""Memory wiring: effective turns are captured, the orchestrator picks up the
injected memory, and the SQL writer surfaces it as worked examples."""

import sqlite3
from types import SimpleNamespace

import pytest

from dbaide.agent.sql_writer import SQLWriter
from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.history.memory_store import MemoryStore
from dbaide.models import ColumnInfo, ConnectionConfig


@pytest.fixture
def service(tmp_path):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="shop", type="sqlite", path=str(db)), make_default=True)
    from dbaide.desktop.service import DesktopService
    svc = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    svc.memory = MemoryStore(base_dir=tmp_path / "memory")
    return svc


def _result(*, status="completed", sql="SELECT COUNT(*) FROM users", pending=""):
    return SimpleNamespace(
        status=SimpleNamespace(value=status), pending_question=pending,
        answer_markdown="a", answer_plaintext="a", selected_sql=sql,
        workflow_id="wf1", trace=[], created_at=0.0, to_dict=dict,
    )


def _req(q="how many users"):
    return SimpleNamespace(question=q, execution_policy=SimpleNamespace(value="safe_auto"))


def test_completed_turn_is_remembered(service):
    service._record_session_turn("shop", "", _req("how many users"), _result(), "")
    items = service.memory.all("shop")
    assert len(items) == 1 and items[0]["sql"] == "SELECT COUNT(*) FROM users"


def test_clarification_and_no_sql_are_not_remembered(service):
    service._record_session_turn("shop", "", _req(), _result(status="wait_user", pending="which?"), "")
    service._record_session_turn("shop", "", _req("vague"), _result(sql=""), "")
    assert service.memory.all("shop") == []


def test_failed_turn_is_not_remembered(service):
    service._record_session_turn("shop", "", _req(), _result(status="failed"), "")
    assert service.memory.all("shop") == []


def test_ask_payload_threads_relevant_memory(service, monkeypatch):
    # Seed a worked example, then capture the WorkflowRequest the service builds.
    service.memory.add("shop", question="how many users", sql="SELECT COUNT(*) FROM users")
    captured = {}

    class _Engine:
        def __init__(self, *a, **k): ...
        def run(self, request, **kw):
            captured["memory"] = request.memory
            return _result()

    monkeypatch.setattr("dbaide.desktop.service.WorkflowEngine", _Engine)
    service.ask({"connection_name": "shop", "question": "number of users", "show_trace": False})
    assert "SELECT COUNT(*) FROM users" in captured["memory"]  # injected into the request


def test_reset_loop_state_picks_up_run_memory(tmp_path):
    from dbaide.agent.orchestrator import AskOrchestrator
    from dbaide.adapters import build_adapter
    from dbaide.joins import JoinCatalogStore
    from dbaide.llm import NullLLMClient
    from dbaide.session import Session

    db = tmp_path / "x.db"
    sqlite3.connect(db).close()
    conn = ConnectionConfig(name="x", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), NullLLMClient(),
                           asset_store=AssetStore(tmp_path / "a"), join_catalog=JoinCatalogStore(base_dir=tmp_path / "j"))
    orch._run_memory = "KNOWN: foo"
    orch._reset_loop_state("q", "", True)
    assert orch._loop_memory == "KNOWN: foo"


def test_sql_writer_surfaces_examples():
    captured = {}

    class _LLM:
        def complete_json(self, messages, *, schema_hint=""):
            captured["user"] = messages[-1].content
            return {"sql": "SELECT 1", "rationale": "", "confidence": 0.9}
        def complete_text(self, messages):
            return "OK"

    w = SQLWriter(_LLM(), dialect="sqlite")
    w.write("how many", "users", [ColumnInfo(name="id", data_type="int")],
            context={"examples": "Known answers:\n- \"how many users\" → SELECT COUNT(*) FROM users"})
    assert "SELECT COUNT(*) FROM users" in captured["user"]  # examples reached the prompt
