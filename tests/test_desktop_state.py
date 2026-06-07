from dbaide.desktop.conversation_state import ThinkingUiState, TurnTraceState
from dbaide.desktop.trace_state import InlineTraceState
from dbaide.desktop.ui_state import ConversationRunState
from dbaide.desktop.ui_state import UiStateBinder


def test_conversation_run_state_tracks_queue_and_remaps_slots():
    state = ConversationRunState(max_runs=2)
    state.active_key = "new:1"
    state.runs["new:1"] = object()
    state.slot_question["new:1"] = "count paid orders"
    state.slot_trace["new:1"] = [{"stage": "execute_sql"}]
    state.slot_connection["new:1"] = "local"
    state.queue_run("new:2", {"question": "queued"})

    assert state.is_active_running() is True
    assert state.active_count() == 2
    assert state.running_ids() == {"new:1", "new:2"}
    assert state.pending_rows() == [
        {"key": "new:1", "title": "count paid orders"},
        {"key": "new:2", "title": ""},
    ]

    state.remap("new:1", "sess-1")

    assert state.active_key == "sess-1"
    assert "sess-1" in state.runs
    assert state.slot_question["sess-1"] == "count paid orders"
    assert state.slot_trace["sess-1"] == [{"stage": "execute_sql"}]
    assert state.slot_connection["sess-1"] == "local"


def test_thinking_state_transitions_do_not_keep_stale_running_flags():
    state = ThinkingUiState()

    state.start("Resolving schema")
    assert state.running is True
    assert state.waiting is False

    state.set_waiting("Need a timezone")
    assert state.running is False
    assert state.waiting is True
    assert state.phase == "Need a timezone"

    state.set_done(ok=True, step_count=2, events=[{"stage": "execute_sql"}])
    assert state.running is False
    assert state.waiting is False
    assert state.ok is True
    assert state.step_count == 2
    assert state.events == [{"stage": "execute_sql"}]


def test_turn_trace_state_final_trace_replaces_live_events():
    state = TurnTraceState()
    state.append({"stage": "discover_schema", "status": "running"})
    state.append({"stage": "discover_schema", "status": "completed"})

    state.set_final([{"stage": "execute_sql", "status": "completed"}])

    assert state.final is True
    assert state.events == [{"stage": "execute_sql", "status": "completed"}]


def test_inline_trace_state_live_and_final_model_lifecycle():
    state = InlineTraceState()
    assert state.is_empty() is True

    state.begin_live()
    state.append_live_event({"stage": "execute_sql", "title": "Calling", "status": "running", "kind": "tool"})
    assert state.model is not None
    assert state.model.overall == "running"
    assert state.is_empty() is False

    state.end_live()
    assert state.model is not None
    assert state.model.overall == "done"

    state.clear()
    assert state.model is None
    assert state.is_empty() is True


def test_ui_state_binder_routes_busy_and_refresh_controls(monkeypatch):
    import dbaide.desktop.ui_state as ui_state

    monkeypatch.setattr(ui_state.sip, "isdeleted", lambda _obj: False)

    calls: list[tuple] = []

    class Dialog:
        def set_save_busy(self, busy, *, target):
            calls.append(("save", busy, target))

        def set_test_busy(self, busy, *, target):
            calls.append(("test", busy, target))

    class Sidebar:
        def set_node_refreshing(self, node, refreshing):
            calls.append(("node", node, refreshing))

    class Topbar:
        def set_global_status(self, text, state):
            calls.append(("status", text, state))

    window = type("Window", (), {"sidebar": Sidebar(), "topbar": Topbar()})()
    binder = UiStateBinder(window)

    binder.set_settings_busy(Dialog(), "save", True, target="connection")
    binder.set_settings_busy(Dialog(), "test", False, target="model")
    binder.set_node_refreshing({"path": "conn.db"}, True)
    binder.global_status("Syncing", "building")

    assert calls == [
        ("save", True, "connection"),
        ("test", False, "model"),
        ("node", {"path": "conn.db"}, True),
        ("status", "Syncing", "building"),
    ]
