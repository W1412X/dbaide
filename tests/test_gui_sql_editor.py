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
    e.set_schema({"tables": ["users", "orders"], "columns_by_table": {"orders": ["user_id"]}})
    words = set(e.completion_names())
    assert "SELECT" in words and "JOIN" in words  # keywords
    assert {"users", "orders", "user_id"} <= words  # schema identifiers


def test_insert_completion_replaces_current_word(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({"tables": ["users"], "columns_by_table": {}})
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
    from dbaide.desktop.components.sql_editor import _QUALIFIED_DOT
    assert _QUALIFIED_DOT.search("SELECT * FROM orders WHERE orders.cit").group(1) == "orders"
    assert _QUALIFIED_DOT.search("FROM analysis.orders.").group(1) == "analysis.orders"
    assert _QUALIFIED_DOT.search("o.").group(1) == "o"
    assert _QUALIFIED_DOT.search("plain") is None


def test_insert_completion_strips_column_type(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("SELECT orders.")
    tc = e.textCursor()
    tc.movePosition(QTextCursor.MoveOperation.End)
    e.setTextCursor(tc)
    e._insert_completion("id · INTEGER")
    assert e.toPlainText() == "SELECT orders.id"


def test_column_labels_include_types(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "columns_by_qualified": {"main.orders": ["id", "amount"]},
        "column_types": {"main.orders.id": "INTEGER", "main.orders.amount": "DECIMAL"},
    })
    assert e._column_labels("main.orders") == ["id · INTEGER", "amount · DECIMAL"]


def test_dialect_keywords_merge(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_dialect("postgres")
    words = set(e.completion_names())
    assert "RETURNING" in words
    assert "SELECT" in words


def test_cascading_completion_db_table_column(qapp):
    """db. → that db's tables; table. (and db.table.) → that table's columns."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "databases": ["analysis", "platform"],
        "tables": ["orders", "sys_user"],
        "columns_by_table": {"orders": ["id", "amount"], "sys_user": ["user_id", "del_flag"]},
        "columns_by_qualified": {
            "analysis.orders": ["id", "amount"],
            "platform.sys_user": ["user_id", "del_flag"],
        },
        "tables_by_database": {"analysis": ["orders"], "platform": ["sys_user"]},
        "column_types": {
            "orders.id": "INTEGER",
            "orders.amount": "INTEGER",
            "analysis.orders.id": "INTEGER",
            "analysis.orders.amount": "INTEGER",
            "sys_user.user_id": "BIGINT",
            "sys_user.del_flag": "CHAR",
        },
    })
    assert e._scoped_words("SELECT * FROM analysis.") == (["orders"], "db:analysis")
    assert e._scoped_words("SELECT orders.")[0] == ["id · INTEGER", "amount · INTEGER"]
    assert e._scoped_words("FROM analysis.orders.")[0] == ["id · INTEGER", "amount · INTEGER"]
    assert e._scoped_words("WHERE sys_user.del")[0] == ["user_id · BIGINT", "del_flag · CHAR"]
    assert e._scoped_words("SELECT id") == (None, "")                        # no dotted scope


def test_alias_completion_from_clause(qapp):
    """FROM orders o  →  o. completes with order columns."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "tables": ["orders", "users"],
        "columns_by_table": {"orders": ["id", "amount"], "users": ["id", "email"]},
        "column_types": {"orders.id": "INTEGER", "orders.amount": "DECIMAL"},
    })
    e.setPlainText("SELECT o.id FROM orders o")
    words, mode = e._scoped_words("SELECT o.")
    assert mode.startswith("alias:")
    assert "id · INTEGER" in words
    assert "amount · DECIMAL" in words


def test_alias_completion_as_syntax(qapp):
    """FROM users AS u  →  u. completes with user columns."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "tables": ["users"],
        "columns_by_table": {"users": ["id", "email"]},
    })
    e.setPlainText("SELECT u.email FROM users AS u")
    words, mode = e._scoped_words("SELECT u.")
    assert words is not None
    assert "email" in words


def test_alias_completion_join(qapp):
    """JOIN table alias  →  alias. completes."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "tables": ["orders", "products"],
        "columns_by_table": {"orders": ["id"], "products": ["name", "price"]},
    })
    e.setPlainText("SELECT p.name FROM orders o JOIN products p ON p.id = o.id")
    words, _ = e._scoped_words("SELECT p.")
    assert words is not None
    assert "name" in words


def test_alias_keyword_not_treated_as_alias(qapp):
    """SQL keywords after FROM should not be treated as aliases."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({
        "tables": ["orders"],
        "columns_by_table": {"orders": ["id"]},
    })
    e.setPlainText("SELECT * FROM orders WHERE id = 1")
    words, _ = e._scoped_words("WHERE.")
    assert words is None


def test_in_string_suppresses_completion(qapp):
    """Completion must not fire inside a string literal."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("SELECT * FROM t WHERE name = 'sel")
    tc = e.textCursor()
    tc.movePosition(QTextCursor.MoveOperation.End)
    e.setTextCursor(tc)
    assert e._in_string() is True


def test_not_in_string_after_closing_quote(qapp):
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("SELECT * FROM t WHERE name = 'hello' AND sel")
    tc = e.textCursor()
    tc.movePosition(QTextCursor.MoveOperation.End)
    e.setTextCursor(tc)
    assert e._in_string() is False


def test_in_string_escaped_quote(qapp):
    """SQL escaped '' inside a string doesn't close it."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("SELECT * WHERE x = 'it''s stil")
    tc = e.textCursor()
    tc.movePosition(QTextCursor.MoveOperation.End)
    e.setTextCursor(tc)
    assert e._in_string() is True


def test_prefix_mid_word(qapp):
    """Cursor in the middle of a word: prefix is only up to cursor."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.setPlainText("SELECT username FROM t")
    tc = e.textCursor()
    # Position cursor after "user" in "username" (offset 11)
    tc.setPosition(11)
    e.setTextCursor(tc)
    assert e._current_prefix() == "user"


def test_insert_completion_mid_word_preserves_suffix(qapp):
    """Completing mid-word should not clobber text after the cursor."""
    from dbaide.desktop.components.sql_editor import SqlEditor
    e = SqlEditor()
    e.set_schema({"tables": ["users"], "columns_by_table": {}})
    e.setPlainText("SELECT usename FROM t")
    # Cursor after "use" in "usename" (position 10)
    tc = e.textCursor()
    tc.setPosition(10)
    e.setTextCursor(tc)
    e._insert_completion("users")
    assert e.toPlainText() == "SELECT usersname FROM t"
