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


def test_load_session_replaces_previous(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.load_session(_turns(), connection="shop")
    tab.load_session([{"question": "only one", "answer_markdown": "ok", "status": "completed",
                       "trace": [], "meta": {}}], connection="shop")
    assert len(tab.conversation._turns) == 1
    assert "only one" in tab.copy_text() and "count paid orders" not in tab.copy_text()
