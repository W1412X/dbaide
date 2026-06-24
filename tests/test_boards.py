"""Saved-question + dashboard model/store tests (no GUI)."""

from __future__ import annotations

from dbaide.boards import Dashboard, SavedQuestion, Tile
from dbaide.boards.models import GRID_COLUMNS
from dbaide.boards.store import DashboardStore, SavedQuestionStore


def _question(**kw) -> SavedQuestion:
    base = dict(name="Sales by region", connection_name="shop", nl_question="各区域销售额",
                sql="SELECT region, sum(amount) AS amount FROM sales GROUP BY 1", database="",
                chart_plan={"chart_type": "bar", "title": "各区域销售额",
                            "category_field": "region", "value_fields": ["amount"]})
    base.update(kw)
    return SavedQuestion(**base)


def test_saved_question_roundtrip_and_refreshable():
    q = _question()
    again = SavedQuestion.from_dict(q.to_dict())
    assert again.name == q.name and again.sql == q.sql and again.chart_plan == q.chart_plan
    assert q.refreshable is True
    # missing sql or plan → static snapshot only
    assert _question(sql="").refreshable is False
    assert _question(chart_plan=None).refreshable is False


def test_from_dict_ignores_unknown_keys():
    q = SavedQuestion.from_dict({"name": "x", "connection_name": "c", "bogus": 1, "id": "abc"})
    assert q.name == "x" and q.id == "abc"


def test_question_store_crud(tmp_path):
    store = SavedQuestionStore(base_dir=tmp_path)
    assert store.list() == []
    q = store.upsert(_question())
    assert [x.id for x in store.list()] == [q.id]
    # upsert by id replaces, preserves created_at
    q.name = "renamed"
    created = store.get(q.id).created_at
    store.upsert(q)
    got = store.get(q.id)
    assert got.name == "renamed" and got.created_at == created
    assert len(store.list()) == 1
    # snapshot update
    store.save_snapshot(q.id, chart_spec={"chart_type": "bar"}, columns=["region", "sum"], row_count=3)
    got = store.get(q.id)
    assert got.row_count == 3 and got.columns == ["region", "sum"] and got.last_run_at
    assert store.delete(q.id) is True and store.list() == []
    assert store.delete(q.id) is False


def test_dashboard_store_and_tiles(tmp_path):
    qstore = SavedQuestionStore(base_dir=tmp_path)
    dstore = DashboardStore(base_dir=tmp_path)
    q1 = qstore.upsert(_question(name="q1"))
    q2 = qstore.upsert(_question(name="q2"))
    board = dstore.create("Overview")
    assert board.tiles == []
    dstore.add_tile(board.id, q1.id)
    board = dstore.add_tile(board.id, q2.id)
    assert [t.question_id for t in board.tiles] == [q1.id, q2.id]
    # second tile stacks below the first (column 0)
    assert board.tiles[0].y == 0
    assert board.tiles[1].y == board.tiles[0].h
    assert all(t.w <= GRID_COLUMNS for t in board.tiles)


def test_detach_question_cascades(tmp_path):
    dstore = DashboardStore(base_dir=tmp_path)
    b1 = dstore.create("a")
    b2 = dstore.create("b")
    dstore.add_tile(b1.id, "Q")
    dstore.add_tile(b2.id, "Q")
    dstore.add_tile(b2.id, "OTHER")
    removed = dstore.detach_question("Q")
    assert removed == 2
    assert [t.question_id for t in dstore.get(b1.id).tiles] == []
    assert [t.question_id for t in dstore.get(b2.id).tiles] == ["OTHER"]


def test_dashboard_roundtrip_preserves_tiles(tmp_path):
    board = Dashboard(name="d", tiles=[Tile("q1", 0, 0, 6, 5), Tile("q2", 6, 0, 6, 5)])
    again = Dashboard.from_dict(board.to_dict())
    assert [t.question_id for t in again.tiles] == ["q1", "q2"]
    assert again.tiles[1].x == 6


def test_refresh_question_reruns_sql_and_rebuilds_chart():
    from dbaide.boards.refresh import refresh_question
    q = _question()  # bar chart, category=region, value=amount

    calls = {}

    def fake_exec(*, connection_name, database, sql):
        calls["sql"] = sql
        # fresh rows (list-of-lists, like a DB cursor) — new values vs the snapshot
        return {"columns": ["region", "amount"], "rows": [["华东", 200], ["华北", 50]], "row_count": 2}

    out = refresh_question(q, fake_exec)
    assert calls["sql"] == q.sql
    assert out["row_count"] == 2
    spec = out["chart_spec"]
    assert spec["chart_type"] == "bar"
    assert spec["categories"] == ["华东", "华北"]  # sorted value_desc
    assert spec["series"][0]["values"] == [200.0, 50.0]


def test_refresh_rejects_non_refreshable():
    from dbaide.boards.refresh import refresh_question
    import pytest
    with pytest.raises(ValueError):
        refresh_question(_question(sql=""), lambda **k: {})


def test_refresh_rejects_pending_confirmation():
    from dbaide.boards.refresh import refresh_question
    import pytest
    with pytest.raises(ValueError):
        refresh_question(_question(), lambda **k: {"pending_confirmation": True})
