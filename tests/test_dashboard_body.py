"""The declarative renderer turns a layout spec into a safe, themed body — and
falls back to an auto-grid when the spec is missing, garbled, or inconsistent."""

from __future__ import annotations

from dbaide.boards.parametric import Combine, ParametricChart, ParamSpec, QuerySource
from dbaide.rendering.dashboard_body import auto_grid, render_body, render_controls


def _chart(cid="c1", params=None):
    return ParametricChart(
        chart_id=cid, title=f"图 {cid}",
        sources=[QuerySource("s", "SELECT a, b FROM s WHERE m=:m")],
        params=params if params is not None else [ParamSpec("m", "date", default="@month_str")],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "a", "value_fields": ["b"]},
    )


def test_render_body_lays_out_tiles_by_kind_and_span():
    charts = [_chart("c1"), _chart("c2")]
    layout = {"rows": [
        {"tiles": [{"kind": "kpi", "chart": "c1", "span": 3, "label": "总额"},
                   {"kind": "chart", "chart": "c1", "span": 9, "height": 320}]},
        {"tiles": [{"kind": "heading", "text": "明细", "span": 12},
                   {"kind": "table", "chart": "c2", "span": 12}]},
    ]}
    body = render_body(layout, charts)
    assert 'data-kind="kpi"' in body and 'data-chart="c1"' in body
    assert "grid-column:span 3" in body and "grid-column:span 9" in body
    assert "height:320px" in body
    assert 'data-kind="table"' in body and "明细" in body
    assert "dbaide-row" in body and "<script" not in body.lower()


def test_render_body_auto_generates_controls_from_params():
    charts = [_chart("c1", [ParamSpec("region", "enum", options=["A", "B"], multi=True, default=["A"])]),
              _chart("c2", [ParamSpec("region", "enum", options=["A", "B"], multi=True),
                            ParamSpec("n", "number", default=5)])]
    layout = {"rows": [{"tiles": [{"kind": "chart", "chart": "c1", "span": 6},
                                  {"kind": "chart", "chart": "c2", "span": 6}]}]}
    body = render_body(layout, charts)
    assert body.count("dbaide-dd") == 1                      # region rendered once (deduped)
    assert 'data-param="n"' in body and 'value="5"' in body
    # multi enum → compact collapsible dropdown with a checklist, default preselected
    assert "<details" in body and "<summary" in body and "dbaide-checklist" in body
    assert 'type="checkbox" data-param="region" value="A" checked' in body
    assert "data-apply" in body


def test_render_body_clamps_span_and_defaults():
    body = render_body({"rows": [{"tiles": [{"kind": "chart", "chart": "c1", "span": 99}]}]}, [_chart("c1")])
    assert "grid-column:span 12" in body                     # clamped to 12
    assert "height:280px" in body                            # default height


def test_render_body_appends_recipes_the_layout_forgot():
    charts = [_chart("c1"), _chart("c2")]
    layout = {"rows": [{"tiles": [{"kind": "chart", "chart": "c1", "span": 12}]}]}   # c2 unplaced
    body = render_body(layout, charts)
    # the model's row is kept AND the forgotten recipe is appended — nothing is lost
    assert "dbaide-row" in body and 'data-chart="c1"' in body
    assert 'data-chart="c2"' in body and "dbaide-grid" in body   # c2 appended in a tail grid


def test_render_body_falls_back_on_empty_or_garbage():
    charts = [_chart("c1")]
    assert "dbaide-grid" in render_body(None, charts)
    assert "dbaide-grid" in render_body({"rows": "nonsense"}, charts)
    assert 'data-chart="c1"' in render_body([], charts)


def test_tiles_referencing_unknown_charts_are_dropped():
    charts = [_chart("c1")]
    # a tile pointing at a non-existent recipe is dropped; since c1 is then uncovered → auto-grid
    body = render_body({"rows": [{"tiles": [{"kind": "chart", "chart": "ghost", "span": 12}]}]}, charts)
    assert "ghost" not in body and 'data-chart="c1"' in body


def test_auto_grid_and_controls_cover_every_chart():
    charts = [_chart("c1"), _chart("c2")]
    grid = auto_grid(charts)
    assert grid.count('data-kind="chart"') == 2 and "data-apply" in grid
    assert render_controls([_chart("c1", [])]) == ""        # no params → no control bar
