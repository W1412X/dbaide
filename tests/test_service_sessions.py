"""DesktopService chat-session actions: CRUD dispatch round-trips and the
ask→turn recording rules (completed turns persist; clarification pauses don't)."""

import sqlite3
from types import SimpleNamespace

import pytest

from dbaide.config import ConfigManager
from dbaide.assets import AssetStore
from dbaide.history.session_store import ChatSessionStore
from dbaide.models import ConnectionConfig


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
    svc.sessions = ChatSessionStore(base_dir=tmp_path / "sessions")
    return svc


def test_session_crud_dispatch(service):
    created = service.dispatch("create_session", {"connection_name": "shop"})
    sid = created["session_id"]
    assert service.dispatch("list_sessions", {"connection_name": "shop"})[0]["session_id"] == sid
    assert service.dispatch("rename_session", {"connection_name": "shop", "session_id": sid, "title": "X"})["renamed"]
    assert service.dispatch("load_session", {"connection_name": "shop", "session_id": sid})["title"] == "X"
    assert service.dispatch("delete_session", {"connection_name": "shop", "session_id": sid})["deleted"]
    assert service.dispatch("list_sessions", {"connection_name": "shop"}) == []


def test_load_missing_session_raises(service):
    with pytest.raises(FileNotFoundError):
        service.dispatch("load_session", {"connection_name": "shop", "session_id": "nope"})


def _fake_result(*, status="completed", pending="", question="q", answer="a", sql="SELECT 1"):
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        pending_question=pending,
        answer_markdown=answer,
        answer_plaintext=answer,
        selected_sql=sql,
        workflow_id="wf1",
        trace=[],
        created_at=0.0,
    )


def _req(question="q"):
    return SimpleNamespace(question=question, execution_policy=SimpleNamespace(value="safe_auto"))


def test_completed_turn_is_recorded(service):
    sid = service._record_session_turn("shop", "", _req("count orders"), _fake_result(), "")
    loaded = service.sessions.load("shop", sid)
    assert len(loaded["turns"]) == 1
    assert loaded["turns"][0]["question"] == "count orders"
    assert loaded["title"] == "count orders"  # auto-titled


def test_clarification_pause_creates_session_without_turn(service):
    sid = service._record_session_turn(
        "shop", "", _req(), _fake_result(status="wait_user", pending="Which amount?"), ""
    )
    loaded = service.sessions.load("shop", sid)
    assert loaded is not None and loaded["turns"] == []  # no turn yet


def test_turns_accumulate_in_same_session(service):
    sid = service._record_session_turn("shop", "", _req("q1"), _fake_result(), "")
    sid2 = service._record_session_turn("shop", sid, _req("q2"), _fake_result(), "")
    assert sid2 == sid
    assert len(service.sessions.load("shop", sid)["turns"]) == 2
