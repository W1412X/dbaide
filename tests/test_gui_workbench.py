import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wb(qapp):
    from dbaide.desktop.views.query_history import QueryHistoryPanel
    from dbaide.desktop.views.workbench import WorkbenchView
    return WorkbenchView(QueryHistoryPanel())


def _titles(wb):
    return [wb.tabs.tabText(i) for i in range(wb.tabs.count())]


def test_starts_with_history_and_one_editor(qapp):
    wb = _wb(qapp)
    assert _titles(wb) == ["History", "Query 1"]


def test_new_sql_editor_increments(qapp):
    wb = _wb(qapp)
    wb.new_sql_editor()
    assert _titles(wb) == ["History", "Query 1", "Query 2"]


def test_open_table_dedupes(qapp):
    wb = _wb(qapp)
    wb.open_table("c", "db", "orders", [])
    wb.open_table("c", "db", "orders", [])  # same table → focus, no dup
    assert _titles(wb).count("orders") == 1
    wb.open_table("c", "db", "users", [])
    assert "users" in _titles(wb)


def test_open_sql_reuses_empty_editor(qapp):
    wb = _wb(qapp)
    ed = wb.open_sql("select 1")            # Query 1 is empty → reused
    assert _titles(wb) == ["History", "Query 1"]
    assert "select 1" in ed.editor.toPlainText()
    wb.open_sql("select 2")                 # current not empty → new editor
    assert _titles(wb) == ["History", "Query 1", "Query 2"]


def test_close_table_and_editor(qapp):
    wb = _wb(qapp)
    wb.open_table("c", "db", "orders", [])
    idx = next(i for i in range(wb.tabs.count()) if wb.tabs.tabText(i) == "orders")
    wb._on_close(idx)
    assert "orders" not in _titles(wb)


def test_history_is_pinned(qapp):
    wb = _wb(qapp)
    wb._on_close(0)  # try to close History
    assert "History" in _titles(wb)


def test_completions_apply_to_all_editors(qapp):
    wb = _wb(qapp)
    wb.new_sql_editor()
    wb.set_sql_completions(["users", "orders"])
    new = wb.new_sql_editor()
    assert "users" in new.editor._model.stringList()  # applied to freshly-created editor too


def test_run_sql_signal(qapp):
    wb = _wb(qapp)
    seen = []
    wb.run_sql.connect(lambda ed, sql: seen.append(sql))
    ed = wb.current_sql_editor()
    ed.set_sql("select 9")
    ed.run_requested.emit("select 9", "execute")
    assert seen == ["select 9"]


def test_structure_panel_relations_and_navigation(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel
    sp = StructurePanel()
    seen = []
    sp.navigate_table.connect(seen.append)
    sp.show_table(
        "orders",
        [{"name": "user_id", "data_type": "INTEGER"}],
        {
            "foreign_keys": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}],
            "referenced_by": [{"table": "shipments", "column": "order_id", "ref_column": "id"}],
        },
    )
    html = sp._relations.text()
    assert 'href="users"' in html and 'href="shipments"' in html
    sp._on_link("users")
    assert seen == ["users"]


def test_structure_panel_no_relations_is_blank(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel
    sp = StructurePanel()
    sp.show_table("t", [{"name": "id", "data_type": "INTEGER", "primary_key": True}], {})
    assert sp._relations.text() == ""


def test_open_table_threads_relations(qapp):
    wb = _wb(qapp)
    rel = {"foreign_keys": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}],
           "referenced_by": []}
    doc = wb.open_table("c", "db", "orders", [{"name": "user_id", "data_type": "INTEGER"}], rel)
    assert 'href="users"' in doc.structure._relations.text()
    got = []
    wb.navigate_table.connect(got.append)
    doc.structure._on_link("users")
    assert got == ["users"]
