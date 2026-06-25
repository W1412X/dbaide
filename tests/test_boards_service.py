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


def test_build_dashboard_app_uses_selected_model(tmp_path, monkeypatch):
    # the dashboard builder must run with the model chosen in the studio
    monkeypatch.setenv("DBAIDE_BOARDS", str(tmp_path / "boards"))
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    import sqlite3
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(region text, amt real); INSERT INTO t VALUES('A',1),('B',2);")
    c.commit(); c.close()
    from dbaide.models import ConnectionConfig, ModelConfig
    svc = DesktopService()
    svc.cfg.upsert_connection(ConnectionConfig(name="shop", type="sqlite", path=str(db)))
    svc.cfg.upsert_model(ModelConfig(name="m1", provider="openai_compatible",
                                     base_url="http://x/v1", api_key="k", model="a"), make_default=True)
    svc.cfg.upsert_model(ModelConfig(name="m2", provider="openai_compatible",
                                     base_url="http://y/v1", api_key="k", model="b"))

    class _Stub:
        def complete_json(self, messages, *, schema_hint=""):
            return {"name": "D", "ui": {"type": "page", "children": [{"type": "chart", "chart": "c1"}]},
                    "charts": [{"chart_id": "c1", "title": "t",
                                "sources": [{"id": "m", "sql": "SELECT region, sum(amt) AS amt FROM t GROUP BY 1"}],
                                "params": [], "combine": {"mode": "single"},
                                "chart_plan": {"chart_type": "bar", "category_field": "region", "value_fields": ["amt"]}}]}

    seen = {}
    monkeypatch.setattr("dbaide.desktop.service.build_llm_client",
                        lambda cfg: (seen.__setitem__("name", cfg.name), _Stub())[1])
    out = svc.dispatch("build_dashboard_app", {"connection_name": "shop", "instruction": "x", "model": "m2"})
    assert seen["name"] == "m2"                 # routed to the chosen model, not the default
    assert out["app"]["name"] == "D"


def test_run_app_chart_enforces_cost_gate(service):
    from dbaide.boards.parametric import Combine, ParametricChart, ParametricDashboard, QuerySource
    chart = ParametricChart(chart_id="c1", title="t", sources=[QuerySource("m", "SELECT a, b FROM t")],
                            params=[], combine=Combine("single"),
                            chart_plan={"chart_type": "bar", "category_field": "a", "value_fields": ["b"]})
    app = ParametricDashboard("d", "shop", charts=[chart])
    service.boards_apps.upsert(app)
    seen = {}

    def cap(payload):
        seen["gate"] = payload.get("enforce_cost_gate")
        return {"columns": ["a", "b"], "rows": [["x", 1]], "row_count": 1}

    service.execute_sql = cap   # capture the flag the dashboard path passes
    service.dispatch("run_app_chart", {"app_id": app.id, "chart_id": "c1", "params": {}})
    assert seen["gate"] is True   # dashboard recipes run through the EXPLAIN cost gate
