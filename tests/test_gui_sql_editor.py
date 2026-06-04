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
    words = set(e.completion_names())
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


def _select_all(e):
    c = e.textCursor()
    c.select(QTextCursor.SelectionType.Document)
    e.setTextCursor(c)


def test_comment_toggle_round_trip(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("select id\nfrom users")
    _select_all(e)
    e.toggle_comment()
    assert e.toPlainText() == "-- select id\n-- from users"
    _select_all(e)
    e.toggle_comment()
    assert e.toPlainText() == "select id\nfrom users"


def test_comment_toggle_single_line(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("select 1")
    e.toggle_comment()
    assert e.toPlainText() == "-- select 1"


def test_comment_toggle_preserves_indent(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("  select 1")
    e.toggle_comment()
    assert e.toPlainText() == "  -- select 1"


def test_line_number_width_grows_with_lines(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("a")
    narrow = e.line_number_area_width()
    e.setPlainText("\n".join(str(i) for i in range(200)))
    assert e.line_number_area_width() > narrow


def test_run_uses_selection_when_present(qapp):
    from dbaide.desktop.views.sql_tab import SqlTab
    from PyQt6.QtGui import QTextCursor
    t = SqlTab()
    t.set_sql("SELECT 1;\nSELECT 2 FROM t;")
    doc = t.editor.document()
    c = t.editor.textCursor()
    c.setPosition(doc.findBlockByNumber(1).position())
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    t.editor.setTextCursor(c)
    assert t._current_sql() == "SELECT 2 FROM t;"


def test_run_uses_statement_at_cursor_without_selection(qapp):
    from dbaide.desktop.views.sql_tab import SqlTab
    t = SqlTab()
    t.set_sql("SELECT 1;\nSELECT 2 FROM t;")
    c = t.editor.textCursor()
    c.setPosition(2)  # inside first statement
    t.editor.setTextCursor(c)
    assert t._current_sql() == "SELECT 1"


def test_set_schema_general_vocab(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "databases": ["main"],
        "tables": ["orders", "users"],
        "columns_by_table": {"orders": ["id", "amount"], "users": ["id", "email"]},
    })
    words = set(e.completion_names())
    assert {"main", "orders", "users", "amount", "email"} <= words  # db+tables+columns
    assert "SELECT" in words  # keywords still present


def test_dot_context_matches_table(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({"tables": ["orders"], "columns_by_table": {"orders": ["id", "amount", "created_at"]}})
    assert e._match_table("orders") == "orders"
    assert e._match_table("ORDERS") == "orders"      # case-insensitive
    assert e._match_table("nope") is None
    assert e._columns_by_table["orders"] == ["id", "amount", "created_at"]


def test_dot_prefix_regex(qapp):
    from dbaide.desktop.components.sql_editor import _DOT_PREFIX
    assert _DOT_PREFIX.search("SELECT * FROM orders WHERE orders.cit").group(1) == "orders"
    assert _DOT_PREFIX.search("o.").group(1) == "o"
    assert _DOT_PREFIX.search("plain") is None


def test_cascading_completion_db_table_column(qapp):
    """db. → that db's tables; table. (and db.table.) → that table's columns."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "databases": ["analysis", "platform"],
        "tables": ["orders", "sys_user"],
        "columns_by_table": {"orders": ["id", "amount"], "sys_user": ["user_id", "del_flag"]},
        "tables_by_database": {"analysis": ["orders"], "platform": ["sys_user"]},
    })
    assert e._scoped_words("SELECT * FROM analysis.") == (["orders"], "db:analysis")
    assert e._scoped_words("SELECT orders.")[0] == ["id", "amount"]
    assert e._scoped_words("FROM analysis.orders.")[0] == ["id", "amount"]   # db.table. → cols
    assert e._scoped_words("WHERE sys_user.del")[0] == ["user_id", "del_flag"]
    assert e._scoped_words("SELECT id") == (None, "")                        # no dotted scope
