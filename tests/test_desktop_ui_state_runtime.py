from __future__ import annotations

from dbaide.desktop.ui_state import BackgroundWorkState, ConversationRunState


def test_conversation_run_state_stores_slot_data_per_session():
    state = ConversationRunState()

    state.set_question("new:1", "count paid orders")
    state.set_trace("new:1", [{"stage": "execute_sql"}])
    state.set_session("new:1", "sess-1")
    state.set_connection("new:1", "shop")
    state.set_pending_resume("new:1", {"question": "count paid orders"})

    assert state.question_for("new:1") == "count paid orders"
    assert state.trace_for("new:1") == [{"stage": "execute_sql"}]
    assert state.session_for("new:1") == "sess-1"
    assert state.connection_for("new:1") == "shop"
    assert state.pending_resume_for("new:1") == {"question": "count paid orders"}


def test_conversation_run_state_compat_mapping_views_roundtrip():
    state = ConversationRunState()

    state.slot_question["new:1"] = "q"
    state.slot_session.update({"new:1": "s1"})
    state.slot_connection["new:1"] = "shop"
    state.slot_trace["new:1"] = [{"stage": "loop"}]
    state.pending_resume["new:1"] = {"question": "q"}

    assert state.question_for("new:1") == "q"
    assert state.session_for("new:1") == "s1"
    assert state.connection_for("new:1") == "shop"
    assert state.trace_for("new:1") == [{"stage": "loop"}]
    assert state.pending_resume_for("new:1") == {"question": "q"}

    del state.slot_connection["new:1"]
    assert state.connection_for("new:1") == ""


def test_conversation_run_state_remap_moves_unified_slot_state():
    state = ConversationRunState()
    state.active_key = "new:1"
    state.runs["new:1"] = object()  # type: ignore[assignment]
    state.queue = [("new:1", {"q": 1})]
    state.set_question("new:1", "q")
    state.set_session("new:1", "sess")
    state.set_trace("new:1", [{"stage": "execute_sql"}])

    state.remap("new:1", "sess")

    assert state.active_key == "sess"
    assert "sess" in state.runs
    assert state.queue[0][0] == "sess"
    assert state.question_for("sess") == "q"
    assert state.session_for("sess") == "sess"
    assert state.trace_for("sess") == [{"stage": "execute_sql"}]


def test_conversation_run_state_remap_collision_keeps_live_slot():
    """If the target session_id already has a (stale) slot, remapping the live temp
    slot onto it must keep the LIVE state, not discard it (orphaned conversation)."""
    state = ConversationRunState()
    # A stale slot already sits under the server id (e.g. previously loaded session).
    state.set_question("sess", "stale question")
    state.set_trace("sess", [{"stage": "old"}])
    # The live temp slot has the fresh run state.
    state.active_key = "new:1"
    state.set_question("new:1", "live question")
    state.set_trace("new:1", [{"stage": "live"}])
    state.set_pending_resume("new:1", {"resume": True})

    state.remap("new:1", "sess")

    assert state.active_key == "sess"
    assert "new:1" not in state.slots
    assert state.question_for("sess") == "live question"          # live won
    assert state.trace_for("sess") == [{"stage": "live"}]
    assert state.pending_resume_for("sess") == {"resume": True}


def test_conversation_run_state_debug_context_uses_active_slot():
    state = ConversationRunState()
    state.activate("sess-1")
    state.set_question("sess-1", "why slow")
    state.set_trace("sess-1", [{"stage": "discover_schema"}])

    context = state.active_debug_context(connection_name="shop", session_id="sess-1")

    assert context == {
        "connection_name": "shop",
        "session_id": "sess-1",
        "active_slot": "sess-1",
        "trace": [{"stage": "discover_schema"}],
        "question": "why slow",
    }


def test_background_work_state_tracks_busy_and_label_per_connection():
    state = BackgroundWorkState()
    state.push("schema_tree", "shop", "Loading schema")
    state.push("build_assets", "analytics", "Building assets")

    assert state.busy()
    assert state.busy("shop") is True
    assert state.busy("missing") is False
    assert state.label_for("shop") == "Loading schema"
    assert state.label_for("analytics") == "Building assets"

    state.pop("schema_tree", "shop")
    assert state.busy("shop") is False
    assert state.busy("analytics") is True
