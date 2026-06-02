"""Offscreen tests for the chat-session UI plumbing: a saved session's turns
render back into the conversation with their answers, SQL and traces."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _turns():
    return [
        {"question": "count paid orders", "answer_markdown": "There are 3.",
         "selected_sql": "SELECT COUNT(*) FROM orders WHERE status='paid'", "status": "completed",
         "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                    "kind": "tool", "step": 1, "row_count": 3, "duration_ms": 5}],
         "meta": {"database": "", "policy": "safe_auto"}},
        {"question": "and refunded?", "answer_markdown": "Just 1.", "selected_sql": "",
         "status": "completed", "trace": [], "meta": {}},
    ]


def test_ask_tab_load_session_renders_turns(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.load_session(_turns(), connection="shop")
    text = tab.copy_text()
    assert "count paid orders" in text and "and refunded?" in text   # both questions
    assert "There are 3." in text and "Just 1." in text              # both answers
    assert "execute_sql" in text or "SQL" in text                    # turn-1 trace restored
    # the conversation now holds two turn records
    assert len(tab.conversation._turns) == 2


def _drain(qapp):
    from PyQt6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(3000)
    for _ in range(8):
        qapp.processEvents()


def test_open_and_new_session_through_main_window(qapp, tmp_path):
    """End-to-end: a seeded session opens into the conversation and highlights in
    the Chats list; New chat clears it. Exercises the async load path."""
    import sqlite3
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.history.session_store import ChatSessionStore, make_turn
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    conn.commit(); conn.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    service.sessions = ChatSessionStore(base_dir=tmp_path / "sessions")
    s = service.sessions.create("local")
    service.sessions.append_turn("local", s["session_id"], make_turn(question="q one", answer_markdown="a one"))
    service.sessions.append_turn("local", s["session_id"], make_turn(question="q two", answer_markdown="a two"))

    win = MainWindow(service)
    _drain(qapp)  # let bootstrap select the connection + load the Chats list

    win.open_session(s["session_id"])
    _drain(qapp)
    assert win.current_session_id == s["session_id"]
    text = win.ask_tab.copy_text()
    assert "q one" in text and "q two" in text and "a one" in text

    win.new_session()
    _drain(qapp)
    assert win.current_session_id == ""
    assert win.ask_tab.copy_text() == ""

    win.deleteLater()
    qapp.processEvents()


def test_load_session_replaces_previous(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.load_session(_turns(), connection="shop")
    tab.load_session([{"question": "only one", "answer_markdown": "ok", "status": "completed",
                       "trace": [], "meta": {}}], connection="shop")
    assert len(tab.conversation._turns) == 1
    assert "only one" in tab.copy_text() and "count paid orders" not in tab.copy_text()
