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
