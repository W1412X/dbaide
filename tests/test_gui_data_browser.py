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
