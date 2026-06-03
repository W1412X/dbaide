"""SQL editor autocomplete vocabulary + completion insertion (non-interactive parts)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QTextCursor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_completion_vocab_merges_keywords_and_schema(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_completions(["users", "orders", "user_id"])
    words = set(e._model.stringList())
    assert "SELECT" in words and "JOIN" in words  # keywords
    assert {"users", "orders", "user_id"} <= words  # schema identifiers


def test_insert_completion_replaces_current_word(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_completions(["users"])
    e.setPlainText("SELECT * FROM use")
    tc = e.textCursor(); tc.movePosition(QTextCursor.MoveOperation.End); e.setTextCursor(tc)
    assert e._current_prefix() == "use"
    e._insert_completion("users")
    assert e.toPlainText() == "SELECT * FROM users"
