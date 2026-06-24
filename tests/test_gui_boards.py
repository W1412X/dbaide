"""GUI-level dashboard tests: tab loads tiles, background refresh updates them."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_BOARDS", str(tmp_path / "boards"))
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    from dbaide.desktop.service import DesktopService
    return DesktopService()


def _pin(service, board="销售看板"):
    chart = {
        "chart_id": "chart:1", "chart_type": "bar", "title": "各区域销售额",
        "categories": ["A", "B"], "series": [{"name": "amount", "values": [1, 2]}], "row_count": 2,
        "chart_plan": {"chart_type": "bar", "title": "各区域销售额",
                       "category_field": "region", "value_fields": ["amount"]},
        "source_sql": "SELECT region, sum(amount) AS amount FROM sales GROUP BY 1",
    }
    return service.dispatch("pin_chart", {
        "name": "各区域销售额", "connection_name": "shop", "nl_question": "各区域销售额",
        "sql": chart["source_sql"], "chart_plan": chart["chart_plan"], "chart_spec": chart,
        "row_count": 2, "dashboard_name": board,
    })


def _spin_until(qapp, predicate, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_dashboard_tab_loads_pinned_tile(qapp, service):
    _pin(service)
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    assert tab._picker.count() == 1
    assert len(tab._grid.tile_ids()) == 1


def test_dashboard_refresh_worker_updates_tile(qapp, service, monkeypatch):
    out = _pin(service)
    qid = out["question"]["id"]
    seen = {}

    def fake_execute_sql(payload):
        seen["sql"] = payload["sql"]
        return {"columns": ["region", "amount"], "rows": [["华东", 10], ["华北", 20]], "row_count": 2}

    monkeypatch.setattr(service, "execute_sql", fake_execute_sql)
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tab._on_tile_refresh(qid)
    assert _spin_until(qapp, lambda: tab._worker is None), "refresh worker did not finish"
    assert seen.get("sql", "").startswith("SELECT region")
    # snapshot persisted with fresh rows
    q = service.dispatch("list_saved_questions", {})["questions"][0]
    assert q["row_count"] == 2 and q["last_run_at"]
    # tile chart updated in place
    assert tab._grid.tile(qid).question()["chart_spec"]["categories"] == ["华北", "华东"]


def test_shutdown_stops_inflight_refresh_without_crashing(qapp, service, monkeypatch):
    out = _pin(service)
    qid = out["question"]["id"]

    def slow_exec(payload):
        time.sleep(0.3)
        return {"columns": ["region", "amount"], "rows": [["A", 1]], "row_count": 1}

    monkeypatch.setattr(service, "execute_sql", slow_exec)
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tab._on_tile_refresh(qid)
    assert tab._worker is not None
    tab.shutdown()                 # must cancel + wait + clear, never abort
    assert tab._worker is None


def test_reload_during_refresh_is_safe(qapp, service, monkeypatch):
    out = _pin(service)
    qid = out["question"]["id"]
    monkeypatch.setattr(service, "execute_sql",
                        lambda p: (time.sleep(0.2),
                                   {"columns": ["region", "amount"], "rows": [["A", 1]], "row_count": 1})[1])
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tab._on_tile_refresh(qid)
    tab.reload()                   # stops the worker before rebuilding tiles
    assert tab._worker is None
    assert len(tab._grid.tile_ids()) == 1


def _pin_to(service, board_id, title):
    chart = {"chart_id": "chart:1", "chart_type": "bar", "title": title,
             "categories": ["A"], "series": [{"name": "v", "values": [1]}], "row_count": 1,
             "chart_plan": {"chart_type": "bar", "title": title, "category_field": "c", "value_fields": ["v"]},
             "source_sql": "SELECT 1"}
    return service.dispatch("pin_chart", {
        "name": title, "connection_name": "shop", "nl_question": title, "sql": "SELECT 1",
        "chart_plan": chart["chart_plan"], "chart_spec": chart, "row_count": 1,
        "dashboard_id": board_id})["question"]["id"]


def test_tile_resize_persists_footprint(qapp, service):
    from PyQt6.QtCore import QPoint
    bid = service.dispatch("create_dashboard", {"name": "b"})["dashboard"]["id"]
    q1 = _pin_to(service, bid, "一")
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    grid = tab._grid
    grid._on_resize_drag(q1, QPoint(400, 200))   # drag the grip far → grow
    grid._on_resize_drop(q1)
    tiles = service.dispatch("get_dashboard", {"id": bid})["dashboard"]["tiles"]
    w = next(t["w"] for t in tiles if t["question_id"] == q1)
    assert w == 12   # clamped to the grid width; persisted via save_dashboard_layout


def test_tile_reorder_persists_order(qapp, service):
    from PyQt6.QtCore import QPoint
    bid = service.dispatch("create_dashboard", {"name": "b"})["dashboard"]["id"]
    q1 = _pin_to(service, bid, "一")
    q2 = _pin_to(service, bid, "二")
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    grid = tab._grid
    assert grid.tile_ids() == [q1, q2]
    grid._drag_pos = QPoint(0, 100000)           # drop far below everything → append
    grid._on_reorder_drop(q1)
    tiles = service.dispatch("get_dashboard", {"id": bid})["dashboard"]["tiles"]
    assert [t["question_id"] for t in tiles] == [q2, q1]


def test_tile_rename_persists(qapp, service):
    out = _pin(service)
    qid = out["question"]["id"]
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tile = tab._grid.tile(qid)
    tile._begin_rename()
    tile._editor.setText("季度销售额")
    tile._commit_rename()
    q = service.dispatch("list_saved_questions", {})["questions"][0]
    assert q["name"] == "季度销售额"


def test_remove_tile_keeps_question_in_library(qapp, service):
    out = _pin(service)
    qid, did = out["question"]["id"], out["dashboard"]["id"]
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tab._on_tile_remove(qid)
    assert len(tab._grid.tile_ids()) == 0
    assert service.dispatch("get_dashboard", {"id": did})["dashboard"]["tiles"] == []
    # the saved question itself must survive (it may be on other boards)
    assert any(q["id"] == qid for q in service.dispatch("list_saved_questions", {})["questions"])
