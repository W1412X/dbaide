"""Parametric dashboard app: model, store, and the run/CRUD service wiring."""

from __future__ import annotations

import pytest

from dbaide.boards.parametric import (
    Combine, ParamSpec, ParametricChart, ParametricDashboard, QuerySource,
)
from dbaide.boards.store import ParametricDashboardStore


def _chart(cid, params):
    return ParametricChart(
        chart_id=cid, title=cid,
        sources=[QuerySource("s", f"SELECT region, sum(amt) AS amt FROM sales WHERE month=:month GROUP BY 1")],
        params=params, combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]},
    )


def test_app_controls_dedup_across_charts():
    app = ParametricDashboard("x", "conn", charts=[
        _chart("a", [ParamSpec("month", "text", "月份")]),
        _chart("b", [ParamSpec("month", "text", "月份"), ParamSpec("region", "enum", "区域")]),
    ])
    assert [p.name for p in app.controls()] == ["month", "region"]   # shared month appears once
    assert app.default_params() == {"month": None, "region": None}


def test_app_roundtrip(tmp_path):
    app = ParametricDashboard("销售看板", "shop", charts=[_chart("c1", [ParamSpec("month", "text", default="2024-03")])])
    again = ParametricDashboard.from_dict(app.to_dict())
    assert again.name == "销售看板" and again.connection_name == "shop"
    assert [c.chart_id for c in again.charts] == ["c1"]
    assert again.charts[0].params[0].default == "2024-03"


def test_app_store_crud(tmp_path):
    store = ParametricDashboardStore(base_dir=tmp_path)
    app = store.upsert(ParametricDashboard("b", "shop", charts=[_chart("c1", [ParamSpec("month", "text")])]))
    assert [a.id for a in store.list()] == [app.id]
    assert store.get(app.id).name == "b"
    assert store.delete(app.id) and store.list() == []


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_BOARDS", str(tmp_path / "boards"))
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    from dbaide.desktop.service import DesktopService
    return DesktopService()


def test_service_run_app_chart_is_deterministic(service, monkeypatch):
    app = service.boards_apps.upsert(
        ParametricDashboard("销售", "shop", charts=[_chart("c1", [ParamSpec("month", "text", default="2024-03")])]))
    seen = {}

    def fake_execute_sql(payload):
        seen["sql"] = payload["sql"]
        return {"columns": ["region", "amt"], "rows": [["华东", 9], ["华北", 4]], "row_count": 2}

    monkeypatch.setattr(service, "execute_sql", fake_execute_sql)
    out = service.dispatch("run_app_chart", {"app_id": app.id, "chart_id": "c1", "params": {"month": "2024-06"}})
    assert "month='2024-06'" in seen["sql"]              # param bound, no LLM
    assert out["chart_spec"]["categories"] == ["华东", "华北"]


def test_service_app_crud_dispatch(service):
    app = service.boards_apps.upsert(
        ParametricDashboard("看板", "shop", charts=[_chart("c1", [ParamSpec("month", "text", "月份")])]))
    apps = service.dispatch("list_dashboard_apps", {})["apps"]
    assert len(apps) == 1 and apps[0]["charts"] == 1
    got = service.dispatch("get_dashboard_app", {"id": app.id})
    assert [c["name"] for c in got["controls"]] == ["month"]
    assert service.dispatch("delete_dashboard_app", {"id": app.id})["deleted"] is True
