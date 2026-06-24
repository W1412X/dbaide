"""Dashboard service wiring: pin → board → refresh → delete, via dispatch()."""

from __future__ import annotations

import os

import pytest

from dbaide.desktop.service import DesktopService


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_BOARDS", str(tmp_path / "boards"))
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    return DesktopService()


def _chart_payload(**kw):
    base = dict(
        name="各区域销售额",
        connection_name="shop",
        nl_question="各区域销售额对比",
        sql="SELECT region, sum(amount) AS amount FROM sales GROUP BY 1",
        chart_plan={"chart_type": "bar", "title": "各区域销售额",
                    "category_field": "region", "value_fields": ["amount"]},
        chart_spec={"chart_type": "bar", "categories": ["A"], "series": [{"name": "amount", "values": [1]}]},
        columns=["region", "amount"],
        row_count=1,
    )
    base.update(kw)
    return base


def test_pin_creates_question_and_board(service):
    out = service.dispatch("pin_chart", {**_chart_payload(), "dashboard_name": "销售看板"})
    assert out["question"]["name"] == "各区域销售额"
    assert out["dashboard"]["name"] == "销售看板"
    assert len(out["dashboard"]["tiles"]) == 1

    boards = service.dispatch("list_dashboards", {})["dashboards"]
    assert len(boards) == 1
    board = service.dispatch("get_dashboard", {"id": boards[0]["id"]})
    qid = out["question"]["id"]
    assert qid in board["questions"]
    assert board["questions"][qid]["refreshable"] is True


def test_pin_to_existing_board_then_refresh(service, monkeypatch):
    board = service.dispatch("create_dashboard", {"name": "b1"})["dashboard"]
    out = service.dispatch("pin_chart", {**_chart_payload(), "dashboard_id": board["id"]})
    qid = out["question"]["id"]

    # stub execute_sql → fresh rows; refresh must rebuild deterministically (no LLM)
    def fake_execute_sql(payload):
        assert payload["sql"].startswith("SELECT region")
        return {"columns": ["region", "amount"], "rows": [["华东", 9], ["华北", 4]], "row_count": 2}

    monkeypatch.setattr(service, "execute_sql", fake_execute_sql)
    res = service.dispatch("refresh_saved_question", {"id": qid})
    assert res["refreshable"] is True and res["row_count"] == 2
    assert res["chart_spec"]["categories"] == ["华东", "华北"]
    # snapshot persisted
    q = service.dispatch("list_saved_questions", {})["questions"][0]
    assert q["row_count"] == 2 and q["last_run_at"]


def test_delete_question_detaches_tiles(service):
    out = service.dispatch("pin_chart", {**_chart_payload(), "dashboard_name": "b"})
    qid, did = out["question"]["id"], out["dashboard"]["id"]
    res = service.dispatch("delete_saved_question", {"id": qid})
    assert res["deleted"] is True and res["tiles_removed"] == 1
    assert service.dispatch("get_dashboard", {"id": did})["dashboard"]["tiles"] == []


def test_refresh_non_refreshable_returns_snapshot(service):
    out = service.dispatch("save_question", {**_chart_payload(sql="", chart_plan=None)})
    res = service.dispatch("refresh_saved_question", {"id": out["question"]["id"]})
    assert res["refreshable"] is False
