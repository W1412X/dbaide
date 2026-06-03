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


def test_table_document_opens_structure_without_query(qapp):
    from dbaide.desktop.views.table_document import TableDocument
    fired = []
    doc = TableDocument("c", "db", "orders")
    doc.query_requested.connect(lambda p: fired.append(p))
    doc.open([{"name": "id", "data_type": "INTEGER", "primary_key": True}])
    assert doc.tabs.currentIndex() == doc._structure_index
    assert doc._data_loaded is False
    assert fired == []  # opening a table must NOT auto-query


def test_table_document_lazy_loads_data_on_tab_switch(qapp):
    from dbaide.desktop.views.table_document import TableDocument
    fired = []
    doc = TableDocument("c", "db", "orders")
    doc.query_requested.connect(lambda p: fired.append(p))
    doc.open([{"name": "id", "data_type": "INTEGER"}])
    doc.focus_data()  # user opens the Data tab → first query now
    assert doc._data_loaded is True
    assert len(fired) == 1 and fired[0]["table"] == "orders"
    doc.focus_structure()
    doc.focus_data()  # returning to Data must not re-query
    assert len(fired) == 1


def test_reopen_table_keeps_subtab(qapp):
    wb = _wb(qapp)
    doc = wb.open_table("c", "db", "orders", [])
    doc.focus_data()
    assert doc.tabs.currentIndex() == doc._data_index
    wb.open_table("c", "db", "orders", [])  # re-open → bring forward, keep sub-tab
    assert doc.tabs.currentIndex() == doc._data_index


def test_structure_panel_indexes(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel
    sp = StructurePanel()
    sp.show_table(
        "orders",
        [{"name": "id", "data_type": "INTEGER", "primary_key": True}],
        {},
        [
            {"name": "idx_user", "columns": ["user_id"], "unique": False, "primary": False},
            {"name": "idx_uniq", "columns": ["a", "b"], "unique": True, "primary": False},
            {"name": "pk_auto", "columns": ["id"], "unique": True, "primary": True},
        ],
    )
    txt = sp._indexes.text()
    assert "idx_user (user_id)" in txt
    assert "idx_uniq (a, b) UNIQUE" in txt
    assert "pk_auto" not in txt  # primary index is omitted (PK shown in grid)


def test_structure_panel_no_indexes_blank(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel
    sp = StructurePanel()
    sp.show_table("t", [{"name": "id", "data_type": "INTEGER"}], {}, [])
    assert sp._indexes.text() == ""


def test_explain_routes_to_explain_signal(qapp):
    wb = _wb(qapp)
    ran, explained = [], []
    wb.run_sql.connect(lambda ed, sql: ran.append(sql))
    wb.explain_sql.connect(lambda ed, sql: explained.append(sql))
    ed = wb.current_sql_editor()
    ed.run_requested.emit("select 1", "explain")
    assert explained == ["select 1"] and ran == []
    ed.run_requested.emit("select 2", "execute")
    assert ran == ["select 2"] and explained == ["select 1"]


def test_close_table_docs_keeps_editors(qapp):
    wb = _wb(qapp)
    wb.new_sql_editor("select 1")
    wb.open_table("c", "db", "orders", [])
    wb.open_table("c", "db", "users", [])
    titles = [wb.tabs.tabText(i) for i in range(wb.tabs.count())]
    assert "orders" in titles and "users" in titles
    wb.close_table_docs()
    after = [wb.tabs.tabText(i) for i in range(wb.tabs.count())]
    assert "orders" not in after and "users" not in after
    assert "History" in after and any(t.startswith("Query") for t in after)
