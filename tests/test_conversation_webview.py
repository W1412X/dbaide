"""The single-view conversation facade (ConversationWebView) is API-compatible with
the old ConversationView and keeps a full model even without WebEngine, so these run
headless (the JS push is a no-op; the model is asserted directly)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from dbaide.desktop.components.conversation_webview import ConversationWebView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view() -> ConversationWebView:
    # conftest stubs WebEngine → try_create returns None → model-only, no real view.
    return ConversationWebView()


def test_turn_lifecycle_and_finalize(qapp):
    v = _view()
    v.begin_turn("How many orders?", meta="shop · main")
    assert v.has_open_turn() is True
    v.append_answer_chunk("There are ")
    v.append_answer_chunk("42 orders.")
    v._flush_stream()
    assert v._turns[0]["_answer"] == "There are 42 orders."

    v.complete_turn(
        answer="There are **42** orders.",
        charts=[{"chart_id": "c1", "chart_type": "bar", "title": "X",
                 "categories": ["a", "b"], "series": [{"name": "n", "values": [1, 2]}], "row_count": 2}],
        trace_events=[{"stage": "query_planning"}, {"stage": "final"}],
    )
    assert v.has_open_turn() is False
    turn = v._turns[0]
    assert turn["status"]["state"] == "done"
    assert [b["type"] for b in turn["blocks"]] == ["markdown", "chart"]
    assert "42" in v.copy_text()


def test_user_bubble_and_attachments(qapp):
    v = _view()
    v.begin_turn("q", meta="m", attachments=[{"name": "orders"}, {"table": "users"}])
    user = v._turns[0]["user"]
    assert user["text"] == "q" and user["meta"] == "m"
    assert [a["name"] for a in user["attachments"]] == ["orders", "users"]


def test_trace_events_drive_phase_and_agenda(qapp):
    v = _view()
    v.begin_turn("q")
    v.append_trace("Linking schema…")
    assert v._turns[0]["status"]["phase"] == "Linking schema…"
    v.append_trace_event({"stage": "query_planning", "kind": "phase"})
    assert v._turns[0]["status"]["state"] == "running"
    assert isinstance(v._turns[0]["agenda"], list)


def test_clarification_round_trip(qapp):
    v = _view()
    v.begin_turn("ambiguous")
    proxy = v.append_clarification(question="Which region?", options=["North", "South"])
    assert v.has_open_turn() is True  # waiting state counts as open
    assert v._turns[0]["clarification"]["mode"] == "single"
    got = {}
    proxy.submitted.connect(lambda s: got.__setitem__("v", s))
    v._on_clarify(v._turns[0]["id"], "North")
    assert got["v"] == "North"
    assert v._turns[0]["clarification"] is None


def test_multi_question_clarification_model(qapp):
    v = _view()
    v.begin_turn("q")
    v.append_clarification(question="", options=[],
                           questions=[{"question": "A?", "options": ["1"]},
                                      {"question": "B?", "options": ["2"]}])
    clar = v._turns[0]["clarification"]
    assert clar["mode"] == "multi" and len(clar["questions"]) == 2


def test_finish_turn_error(qapp):
    v = _view()
    v.begin_turn("q")
    v.finish_turn_error("boom")
    assert v.has_open_turn() is False
    assert v._turns[0]["status"]["state"] == "error"
    assert v._turns[0]["notes"][0]["text"] == "boom"


def test_bulk_load_and_clear(qapp):
    v = _view()
    v.begin_bulk_load()
    for i in range(3):
        v.begin_turn(f"q{i}", placeholder=False)
        v.complete_turn(answer=f"a{i}")
    v.end_bulk_load()
    assert len(v._turns) == 3
    assert all(t["status"]["state"] == "done" for t in v._turns)
    v.clear()
    assert v._turns == [] and v.has_open_turn() is False


def test_actions_registered_and_dispatched(qapp):
    v = _view()
    v.begin_turn("q")
    fired = {}
    v.complete_turn(answer="a", actions=[{"id": "0", "label": "Copy"}, {"id": "1", "label": "Export"}],
                    on_action=lambda aid: fired.__setitem__("id", aid))
    turn = v._turns[0]
    assert [a["label"] for a in turn["actions"]] == ["Copy", "Export"]
    # the bridge fires _on_action(turn_id, action_id) → registered dispatcher
    v._on_action(turn["id"], "1")
    assert fired["id"] == "1"


def test_append_clarification_reply_appends_to_user(qapp):
    v = _view()
    v.begin_turn("base question")
    v.append_clarification_reply("my answer")
    assert "my answer" in v._turns[0]["user"]["text"]
    assert v._turns[0]["clarification"] is None
