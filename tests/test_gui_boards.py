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
    assert len(tab._tiles) == 1


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
    assert tab._tiles[qid].question()["chart_spec"]["categories"] == ["华北", "华东"]


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
    assert len(tab._tiles) == 1


def test_remove_tile_keeps_question_in_library(qapp, service):
    out = _pin(service)
    qid, did = out["question"]["id"], out["dashboard"]["id"]
    from dbaide.desktop.views.dashboard_tab import DashboardTab
    tab = DashboardTab(service)
    tab._on_tile_remove(qid)
    assert len(tab._tiles) == 0
    assert service.dispatch("get_dashboard", {"id": did})["dashboard"]["tiles"] == []
    # the saved question itself must survive (it may be on other boards)
    assert any(q["id"] == qid for q in service.dispatch("list_saved_questions", {})["questions"])
