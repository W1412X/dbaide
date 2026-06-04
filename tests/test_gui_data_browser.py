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


def test_structure_panel(qapp):
    from dbaide.desktop.views.structure_panel import StructurePanel, _generate_ddl
    cols = [{"name": "id", "data_type": "INTEGER", "primary_key": True},
            {"name": "city", "data_type": "TEXT", "indexed": True}]
    ddl = _generate_ddl("orders", cols)
    assert "CREATE TABLE orders" in ddl and "id INTEGER PRIMARY KEY" in ddl
    sp = StructurePanel()
    sp.show_table("orders", cols)
    assert len(sp._cols._rows) == 2
    assert sp.stack.currentIndex() == 1


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
