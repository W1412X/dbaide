"""Multi-run concurrency: several sessions run at once, capped by max_concurrent_runs,
with extra runs queued and each run's result routed to its own session slot."""
from __future__ import annotations

import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _drain(qapp, ms=4000):
    QThreadPool.globalInstance().waitForDone(ms)
    for _ in range(10):
        qapp.processEvents()


def _make_window(tmp_path):
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    return MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))


class _FakeWorker:
    is_cancelled = False
    def cancel(self):
        self.is_cancelled = True


class _FakeDoc:
    def __init__(self):
        self.running: list[bool] = []
        self.result = None
        self.error = ""

    def set_running(self, running: bool):
        self.running.append(running)

    def show_result(self, result):
        self.result = result

    def show_error(self, message: str):
        self.error = message


class _FakeHistoryStore:
    def __init__(self):
        self.records = []

    def record(self, connection, sql, **kwargs):
        self.records.append((connection, sql, kwargs))

    def recent(self, connection):
        return []


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


def test_runs_over_cap_are_queued(qapp, tmp_path):
    """Starting more conversation runs than the cap queues the overflow rather than
    rejecting it; the active slot's run is tracked per session."""
    win = _make_window(tmp_path)
    _drain(qapp)
    win._max_runs = 2

    # Occupy both slots with fake in-flight runs (so a real launch isn't needed).
    win._runs["A"] = _FakeWorker()
    win._runs["B"] = _FakeWorker()
    win._slot_session.update({"A": "A", "B": "B"})

    # A third session asks → must queue.
    key = "C"
    win.ask_tab.ensure_slot(key); win._active_key = key; win.ask_tab.set_active(key)
    win.conversation_controller.start_ask(key, {"connection_name": "local", "question": "q", "session_id": ""})

    assert key not in win._runs                      # not started (cap reached)
    assert any(k == key for k, _ in win._run_queue)  # queued instead

    # Freeing a slot drains the queue (the fake A finishes → C launches).
    win._runs.pop("A", None)
    win.conversation_controller.drain_queue()
    assert key in win._runs and not any(k == key for k, _ in win._run_queue)

    win._runs.clear(); win._run_queue.clear()
    win.deleteLater(); qapp.processEvents()


def test_two_sessions_run_concurrently_and_route_to_own_slots(qapp, tmp_path):
    """Two real concurrent ask runs each land their answer in their own session view,
    not whichever one happens to be visible."""
    win = _make_window(tmp_path)
    _drain(qapp)
    win._max_runs = 3

    # Session 1 (active) asks.
    win.submit_composer("first question about t")
    key1 = win._active_key
    assert key1 in win._runs

    # Switch to a new session and ask a second question concurrently.
    win.new_session()
    win.submit_composer("second different question")
    key2 = win._active_key
    assert key2 != key1
    assert key1 in win._runs and key2 in win._runs   # both running at once

    _drain(qapp, 8000)

    # Each session's conversation holds its own question (routing was correct).
    # After completion the temp keys remap to server session ids — find both views.
    texts = [v.copy_text() for v in win.ask_tab._views.values()]
    joined = "\n".join(texts)
    assert "first question about t" in joined
    assert "second different question" in joined
    # The two questions live in different views (not merged into one).
    assert not any(("first question about t" in t and "second different question" in t) for t in texts)

    win.deleteLater(); qapp.processEvents()


def test_running_new_chat_appears_as_pending_row(qapp, tmp_path):
    """A brand-new (unsaved) chat that is running shows up in the Chats list as an
    ephemeral row, so it can be switched back to mid-run."""
    win = _make_window(tmp_path)
    _drain(qapp)
    win._max_runs = 3

    win.submit_composer("a question about table t")
    key = win._active_key
    assert key.startswith("new:") and key in win._runs

    # The Chats list now carries an ephemeral running row keyed by the temp slot key.
    sl = win.sidebar.chats
    pending_keys = [str(p.get("key")) for p in sl._pending]
    assert key in pending_keys
    assert key in sl._running_ids  # spinner on
    # Its title reflects the question.
    assert any("a question about table t".startswith(str(p.get("title"))[:10]) or
               str(p.get("title")).startswith("a question") for p in sl._pending)

    # Clicking it routes back to that slot.
    win.open_session(key)
    assert win._active_key == key

    _drain(qapp, 8000)
    # Once it completes it remaps to a server id and the ephemeral row is gone.
    assert not any(str(p.get("key")) == key for p in sl._pending)

    win.deleteLater(); qapp.processEvents()


def test_oneoff_sql_result_routes_to_originating_editor(monkeypatch):
    """A SQL run must clear/write the editor that launched it, not the editor active
    when the worker finishes."""
    import dbaide.desktop.views.main_window as mw

    monkeypatch.setattr(mw.sip, "isdeleted", lambda _obj: False)
    win = mw.MainWindow.__new__(mw.MainWindow)
    origin = _FakeDoc()
    other = _FakeDoc()
    history = _FakeHistoryStore()
    bus = _FakeBus()
    win._oneoff = mw.OneOffState(
        action="execute_sql",
        sql_doc=origin,
        data_doc=None,
        sql="select 1",
        connection="old",
        database="main",
    )
    win._oneoff_worker = object()
    win._active_sql_doc = other
    win._active_data_doc = None
    win._building = False
    win.query_history_store = history
    win.bus = bus
    win.current_connection = lambda: "current"  # type: ignore[method-assign]
    win.current_database = lambda: "other_db"  # type: ignore[method-assign]
    win._refresh_query_history = lambda: None  # type: ignore[method-assign]
    from dbaide.desktop.run_controllers import OneOffActionController

    win.conversation_controller = type("Runs", (), {
        "sync_active_ui": lambda _self: None,
        "refresh_run_status": lambda _self: None,
    })()
    win.oneoff_controller = OneOffActionController(win)

    result = {"columns": ["x"], "rows": [{"x": 1}], "row_count": 1, "elapsed_ms": 7}
    win.oneoff_controller.on_done("execute_sql", result)

    assert origin.running == [False]
    assert origin.result == result
    assert other.running == [] and other.result is None
    assert history.records == [("old", "select 1", {
        "ok": True,
        "row_count": 1,
        "elapsed_ms": 7,
        "database": "main",
    })]
    assert bus.events[-1][1] == {"instance": "old"}
    assert win._oneoff_worker is None
    assert win._oneoff == mw.OneOffState()


def test_query_completed_event_ignores_other_connections():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    calls: list[str] = []
    win.current_connection = lambda: "current"  # type: ignore[method-assign]
    win._load_sessions = lambda conn: calls.append(conn)  # type: ignore[method-assign]

    win._on_query_completed({"instance": "old"})
    assert calls == []

    win._on_query_completed({"instance": "current"})
    assert calls == ["current"]


def test_conversation_controller_active_or_new_key_updates_window_and_ask_tab():
    import dbaide.desktop.views.main_window as mw
    from dbaide.desktop.ui_state import ConversationRunState

    win = mw.MainWindow.__new__(mw.MainWindow)
    active_calls: list[str] = []
    win.run_state = ConversationRunState()
    win.current_session_id = "old"
    win.ask_tab = type("Ask", (), {"set_active": lambda _self, key: active_calls.append(key)})()

    from dbaide.desktop.run_controllers import ConversationRunController

    win.conversation_controller = ConversationRunController(win)
    key = win.conversation_controller.active_or_new_key()

    assert key == "new:1"
    assert win._active_key == "new:1"
    assert win.current_session_id == ""
    assert active_calls == ["new:1"]


def test_refresh_all_failure_clears_loading_status():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    messages: list[str] = []
    restored: list[bool] = []
    background_calls: list[tuple] = []
    win._ensure_ui_state = lambda: type("Ui", (), {  # type: ignore[method-assign]
        "statusbar_message": lambda _self, message, timeout_ms=0: messages.append(message),
    })()
    win._restore_status_badge = lambda **kwargs: restored.append(True)  # type: ignore[method-assign]
    win.conversation_controller = type("Ctrl", (), {  # type: ignore[method-assign]
        "sync_work_ui": lambda _self: restored.append(True),
    })()
    win.toast = lambda message: messages.append(f"toast:{message}")  # type: ignore[method-assign]

    def run_background(action, payload, on_success, *, on_error=None, on_progress=None):
        background_calls.append((action, payload, on_success, on_error, on_progress))

    win._run_background = run_background  # type: ignore[method-assign]

    win.refresh_all()
    assert messages == ["Loading…"]
    assert background_calls[0][0] == "bootstrap"
    assert background_calls[0][3] == win._on_bootstrap_failed

    win._on_bootstrap_failed(RuntimeError("boom"))

    assert restored == [True]
    assert messages[-2:] == ["Load failed: boom", "toast:boom"]


def test_status_badge_restore_respects_owner_token():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    calls: list[tuple[str, list]] = []
    win._status_owner = "sync:old"
    win._asset_work_stack = []
    win.bootstrap = {"connections": [{"name": "current", "asset_status": "ready"}]}
    win.current_connection = lambda: "current"  # type: ignore[method-assign]
    win._assets_busy = lambda conn=None: False  # type: ignore[method-assign]
    win._ensure_ui_state = lambda: type("Ui", (), {  # type: ignore[method-assign]
        "restore_connection_status": lambda _self, conn, conns: calls.append((conn, conns)),
    })()

    win._restore_status_badge(owner="sync:new")
    assert calls == []
    assert win._status_owner == "sync:old"

    win._restore_status_badge(owner="sync:old")
    assert calls == [("current", [{"name": "current", "asset_status": "ready"}])]
    assert win._status_owner == ""


def test_status_badge_restore_skips_while_assets_busy():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    calls: list[tuple[str, list]] = []
    win._status_owner = ""
    win._asset_work_stack = [("refresh_instance", "current", "Syncing")]
    win.bootstrap = {"connections": [{"name": "current", "asset_status": "ready"}]}
    win.current_connection = lambda: "current"  # type: ignore[method-assign]
    win._assets_busy = lambda conn=None: True  # type: ignore[method-assign]
    win._ensure_ui_state = lambda: type("Ui", (), {  # type: ignore[method-assign]
        "restore_connection_status": lambda _self, conn, conns: calls.append((conn, conns)),
    })()

    win._restore_status_badge()
    assert calls == []

    win._restore_status_badge(force=True)
    assert calls == [("current", [{"name": "current", "asset_status": "ready"}])]


def test_pop_asset_work_syncs_composer():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    sync_calls: list[str] = []
    win._asset_work_stack = [("schema_tree", "current", "Loading")]
    win._asset_work_connection = mw.MainWindow._asset_work_connection.__get__(win, mw.MainWindow)  # type: ignore[method-assign]
    win.current_connection = lambda: "current"  # type: ignore[method-assign]
    win.conversation_controller = type("Ctrl", (), {  # type: ignore[method-assign]
        "sync_work_ui": lambda _self: sync_calls.append("sync"),
    })()

    win._pop_asset_work("schema_tree", {"name": "current"})
    assert win._asset_work_stack == []
    assert sync_calls == ["sync"]


def test_stale_ask_completion_is_ignored():
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    calls: list[str] = []
    win._runs = {}
    win.ask_tab = type("Ask", (), {"append_result": lambda *_a: calls.append("append")})()
    from dbaide.desktop.run_controllers import ConversationRunController

    win.conversation_controller = ConversationRunController(win)
    win.conversation_controller.sync_active_ui = lambda: calls.append("sync")  # type: ignore[method-assign]
    win.conversation_controller.refresh_run_status = lambda: calls.append("status")  # type: ignore[method-assign]
    win.conversation_controller.drain_queue = lambda: calls.append("drain")  # type: ignore[method-assign]

    win.conversation_controller.on_done("old", {"status": "completed"})
    win.conversation_controller.on_failed("old", RuntimeError("late failure"))

    assert calls == []
