"""Table data browser: server-side paginated browse_table + the DataBrowser widget."""
from __future__ import annotations

import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _service(tmp_path, rows=10):
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.models import ConnectionConfig
    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT, city TEXT)")
    for i in range(rows):
        c.execute("INSERT INTO t(name, city) VALUES(?,?)", (f"n{i}", ["NYC", "SF", "LA"][i % 3]))
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    return DesktopService(cfg, AssetStore(tmp_path / "assets"))


def test_browse_table_paginates(qapp, tmp_path):
    svc = _service(tmp_path, rows=10)
    p1 = svc.dispatch("browse_table", {"connection_name": "local", "table": "t", "page_size": 4, "offset": 0})
    assert p1["columns"] == ["id", "name", "city"]
    assert len(p1["rows"]) == 4 and p1["has_more"] is True and p1["offset"] == 0
    p2 = svc.dispatch("browse_table", {"connection_name": "local", "table": "t", "page_size": 4, "offset": 8})
    assert len(p2["rows"]) == 2 and p2["has_more"] is False  # last partial page


def test_browse_table_exact_multiple_has_no_phantom_next_page(qapp, tmp_path):
    # Table size is an exact multiple of page_size: the LAST full page must report
    # has_more=False (fetch-one-extra), not enable Next onto an empty page.
    svc = _service(tmp_path, rows=8)
    p1 = svc.dispatch("browse_table", {"connection_name": "local", "table": "t", "page_size": 4, "offset": 0})
    assert len(p1["rows"]) == 4 and p1["has_more"] is True
    p2 = svc.dispatch("browse_table", {"connection_name": "local", "table": "t", "page_size": 4, "offset": 4})
    assert len(p2["rows"]) == 4 and p2["has_more"] is False  # final page, no phantom next
    assert p2["row_count"] == 4


def test_export_table_all_returns_all_rows_unfiltered(qapp, tmp_path):
    # "Export all rows" on an UNFILTERED table must not be capped to the small
    # unfiltered-star bound (≤100) nor blocked by the large-LIMIT cost gate on the
    # default (production) profile. 250 rows must all come back.
    svc = _service(tmp_path, rows=250)
    r = svc.dispatch("export_table_all", {"connection_name": "local", "table": "t"})
    assert len(r["rows"]) == 250 and r["capped"] is False
    # WHERE-filtered export also returns every matching row.
    rf = svc.dispatch("export_table_all", {"connection_name": "local", "table": "t", "where": "city = 'SF'"})
    assert rf["rows"] and all(row["city"] == "SF" for row in rf["rows"])


def test_browse_table_sort_and_filter(qapp, tmp_path):
    svc = _service(tmp_path, rows=9)
    desc = svc.dispatch("browse_table", {"connection_name": "local", "table": "t",
                                         "order_by": "id", "order_dir": "desc", "page_size": 100})
    ids = [r["id"] for r in desc["rows"]]
    assert ids == sorted(ids, reverse=True)
    filt = svc.dispatch("browse_table", {"connection_name": "local", "table": "t",
                                         "where": "city = 'SF'", "page_size": 100})
    assert filt["rows"] and all(r["city"] == "SF" for r in filt["rows"])


def test_browse_table_requires_table(qapp, tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(ValueError):
        svc.dispatch("browse_table", {"connection_name": "local", "table": ""})


def test_data_browser_widget(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    captured = []
    w = DataBrowser()
    w.query_requested.connect(lambda p: captured.append(p))
    w.open_table("local", "main", "orders")
    assert captured and captured[-1]["table"] == "orders" and captured[-1]["offset"] == 0
    # feed a full page back → next enabled, range shown
    w.show_result({"columns": ["id"], "rows": [{"id": i} for i in range(100)],
                   "has_more": True, "offset": 0, "page_size": 100})
    assert w._next.isEnabled() and not w._prev.isEnabled()
    assert "1" in w._range.text()


def test_table_browser_content_uses_clean_margins(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    from dbaide.desktop.views.structure_panel import StructurePanel

    db = DataBrowser()
    db.open_table("local", "main", "orders")
    db_page_margins = db.stack.widget(1).layout().contentsMargins()
    assert (db_page_margins.left(), db_page_margins.top(), db_page_margins.right()) == (16, 10, 16)
    assert db.stack.widget(1).layout().spacing() == 10

    sp = StructurePanel()
    sp.show_table("orders", [{"name": "id", "data_type": "INTEGER"}])
    sp_page_margins = sp.stack.widget(1).layout().contentsMargins()
    assert (sp_page_margins.left(), sp_page_margins.top(), sp_page_margins.right()) == (16, 10, 16)
    assert sp.stack.widget(1).layout().spacing() == 10


def test_structure_panel(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel, _generate_ddl
    cols = [{"name": "id", "data_type": "INTEGER", "primary_key": True},
            {"name": "city", "data_type": "TEXT", "indexed": True}]
    ddl = _generate_ddl("orders", cols)
    assert 'CREATE TABLE "orders"' in ddl and '"id" INTEGER PRIMARY KEY' in ddl
    sp = StructurePanel()
    sp.show_table("orders", cols)
    assert len(sp._cols._rows) == 2
    assert sp.stack.currentIndex() == 1


def test_structure_panel_ddl_quotes_reserved_and_cjk_identifiers(qapp):
    """Generated DDL is copyable, so identifiers must be quoted (reserved words,
    CJK names) and dialect-correct (backticks for mysql)."""
    from dbaide.desktop.views.structure_panel import _generate_ddl
    cols = [{"name": "order", "data_type": "TEXT"}, {"name": "金额", "data_type": "REAL"}]
    generic = _generate_ddl("订单", cols)
    assert 'CREATE TABLE "订单"' in generic and '"order" TEXT' in generic and '"金额" REAL' in generic
    mysql = _generate_ddl("select", cols, "mysql")
    assert "CREATE TABLE `select`" in mysql and "`order` TEXT" in mysql and "`金额` REAL" in mysql


def test_count_table_service(qapp, tmp_path):
    svc = _service(tmp_path, rows=25)
    out = svc.count_table({"connection_name": "local", "table": "t"})
    assert out["count"] == 25
    out2 = svc.count_table({"connection_name": "local", "table": "t", "where": "city = 'NYC'"})
    assert 0 < out2["count"] < 25


def test_data_browser_count_button(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    w = DataBrowser()
    payloads = []
    w.count_requested.connect(payloads.append)
    w.open_table("local", "main", "t")
    w.set_running(False)  # no MainWindow here to clear the loading flag
    w._on_count()
    assert payloads and payloads[0]["table"] == "t"
    w.show_count(123)
    assert "123" in w._count_btn.text()
    # filter change invalidates the total
    w._filter.setText("id > 1"); w._on_filter()
    assert w._total is None
    assert w._count_btn.text() == w._t("data.count")


def test_fk_cell_navigation(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    w = DataBrowser()
    nav = []
    w.navigate_fk.connect(lambda t, c, v: nav.append((t, c, v)))
    w.set_foreign_keys({"user_id": ("users", "id")})
    w.show_result({"columns": ["id", "user_id"], "rows": [{"id": 1, "user_id": 7}], "offset": 0})
    fk_col = w._columns.index("user_id")
    acts = w._fk_cell_actions(0, fk_col)
    assert acts and "users" in acts[0][0]
    acts[0][1]()  # trigger the action
    assert nav == [("users", "id", 7)]
    # a non-FK column offers no navigation
    assert w._fk_cell_actions(0, w._columns.index("id")) == []


def test_fk_filter_where_is_dialect_correct():
    """The FK-navigation WHERE quotes the column and renders the value with the target
    dialect's escaping — a generic literal would mis-handle a backslash on MySQL."""
    from dbaide.desktop.views.main_window import _fk_filter_where
    assert _fk_filter_where("code", "a\\b", "mysql") == "`code` = 'a\\\\b'"   # backslash doubled
    assert _fk_filter_where("code", "a\\b", "sqlite") == '"code" = \'a\\b\''   # backslash literal
    assert _fk_filter_where("id", 7, "postgres") == '"id" = 7'                  # numbers unquoted
    assert _fk_filter_where("code", "O'B", "sqlite") == '"code" = \'O\'\'B\''    # quote escaped


def test_fk_cell_navigation_skips_null_value(qapp):
    """A NULL foreign-key cell offers no navigation — there is no referenced row to
    open (and `ref_column = NULL` would never match)."""
    from dbaide.desktop.views.data_browser import DataBrowser
    w = DataBrowser()
    w.set_foreign_keys({"user_id": ("users", "id")})
    w.show_result({"columns": ["id", "user_id"], "rows": [{"id": 1, "user_id": None}], "offset": 0})
    assert w._fk_cell_actions(0, w._columns.index("user_id")) == []


def test_browse_filtered_sets_identity_and_where(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    w = DataBrowser()
    payloads = []
    w.query_requested.connect(payloads.append)
    w.browse_filtered("local", "main", "users", '"id" = 3')
    assert payloads
    assert payloads[0]["table"] == "users" and payloads[0]["where"] == '"id" = 3'
    assert w._filter.text() == '"id" = 3'


def test_data_browser_sort_via_header_menu(qapp):
    """Sorting is an explicit right-click choice (Ascending/Descending/Clear), not a
    click-to-sort. The provider exposes those actions and they set the sort state."""
    from dbaide.desktop.views.data_browser import DataBrowser
    db = DataBrowser()
    db.open_table("local", "main", "t")   # one initial reload (sets _loading True)
    db._columns = ["id", "name"]
    db._loading = False
    # Header click no longer sorts.
    assert db.grid.table.horizontalHeader().sectionsClickable() is False
    # The provider offers asc + desc for an unsorted column (no Clear yet).
    actions = db._sort_actions(1)
    assert len(actions) == 2
    db._apply_sort("name", "desc")
    assert (db._order_by, db._order_dir) == ("name", "desc")
    db._loading = False
    # Now the active column also offers Clear.
    assert len(db._sort_actions(1)) == 3
    db._clear_sort()
    assert (db._order_by, db._order_dir) == ("", "asc")
    db.deleteLater()


def test_data_browser_shows_loading_state(qapp):
    from dbaide.desktop.views.data_browser import DataBrowser
    db = DataBrowser()
    db.set_running(True)
    assert db._loading is True
    assert ("Loading" in db._range.text()) or ("加载" in db._range.text())
    assert db._prev.isEnabled() is False          # controls locked while loading
    db.set_running(False)
    assert db._loading is False
    assert db._prev.isEnabled() is True
    db.deleteLater()


def test_filter_completes_column_words(qapp):
    """The WHERE filter box completes column names word-by-word (not the whole line)."""
    from dbaide.desktop.views.data_browser import DataBrowser
    db = DataBrowser()
    db.open_table("local", "main", "orders")
    db.show_result({"columns": ["id", "amount", "status"], "rows": [], "row_count": 0, "offset": 0})
    words = db._filter_model.stringList()
    assert {"id", "amount", "status"} <= set(words) and "AND" in words
    db._filter.setText("amount > 10 AND stat")
    db._filter.setCursorPosition(len("amount > 10 AND stat"))
    assert db._filter_word() == ("stat", 16)
    db._insert_filter_completion("status")
    assert db._filter.text() == "amount > 10 AND status"   # only the word is replaced
    db.deleteLater()


def test_filter_completes_cjk_column_words(qapp):
    """The WHERE filter completer must trigger on CJK column names (zh databases)."""
    from dbaide.desktop.views.data_browser import DataBrowser
    db = DataBrowser()
    db.open_table("local", "main", "订单")
    db.show_result({"columns": ["编号", "金额"], "rows": [], "row_count": 0, "offset": 0})
    db._filter.setText("金额 > 10 AND 编")
    db._filter.setCursorPosition(len("金额 > 10 AND 编"))
    assert db._filter_word() == ("编", len("金额 > 10 AND "))
    db._insert_filter_completion("编号")
    assert db._filter.text() == "金额 > 10 AND 编号"
    db.deleteLater()


def test_table_ddl_service_returns_real_sqlite_ddl(qapp, tmp_path):
    """The table_ddl action returns the database's actual CREATE statement (verbatim
    from sqlite_master), not an auto-inferred one."""
    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE orders (\n  id INTEGER PRIMARY KEY,\n  amount REAL NOT NULL,\n  status TEXT DEFAULT 'new'\n)")
    c.commit(); c.close()
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.models import ConnectionConfig
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    svc = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    out = svc.dispatch("table_ddl", {"connection_name": "local", "table": "orders"})
    assert "CREATE TABLE orders" in out["ddl"]
    assert "amount REAL NOT NULL" in out["ddl"]      # real constraints preserved
    assert "DEFAULT 'new'" in out["ddl"]


def test_structure_panel_set_ddl_replaces_generated(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel
    sp = StructurePanel()
    sp.show_table("orders", [{"name": "id", "data_type": "int", "primary_key": True}])
    assert "(generated)" in sp._ddl_label.text() or "自动生成" in sp._ddl_label.text()
    sp.set_ddl("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL NOT NULL);")
    assert "amount REAL NOT NULL" in sp._ddl.toPlainText()   # real DDL shown
    assert "generated" not in sp._ddl_label.text() and "自动生成" not in sp._ddl_label.text()


def test_table_document_requests_ddl_on_open(qapp):
    from dbaide.desktop.views.table_document import TableDocument
    doc = TableDocument("conn", "main", "orders")
    payloads = []
    doc.ddl_requested.connect(payloads.append)
    doc.open([{"name": "id", "data_type": "int", "primary_key": True}])
    assert payloads and payloads[0]["table"] == "orders"
    doc.show_ddl("CREATE TABLE orders (id INTEGER);")
    assert "CREATE TABLE orders" in doc.structure._ddl.toPlainText()
    doc.deleteLater()
