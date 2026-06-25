"""The component-tree renderer turns a nestable UI spec into a safe, themed body —
composing rows/sections/tabs/grids + chart/kpi/table/text, with graceful fallback."""

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


def test_render_tree_composes_rows_kinds_and_spans():
    charts = [_chart("c1"), _chart("c2")]
    ui = {"type": "page", "children": [
        {"type": "row", "children": [
            {"type": "kpi", "chart": "c1", "span": 3, "label": "总额"},
            {"type": "chart", "chart": "c1", "span": 9, "height": 320}]},
        {"type": "section", "title": "明细", "children": [
            {"type": "table", "chart": "c2"}]},
    ]}
    body = render_body(ui, charts)
    assert 'data-kind="kpi"' in body and "grid-column:span 3" in body and "grid-column:span 9" in body
    assert "height:320px" in body
    assert "dbaide-section" in body and "明细" in body and 'data-kind="table"' in body
    assert "<script" not in body.lower()


def test_render_tree_tabs_and_markdown():
    charts = [_chart("c1"), _chart("c2")]
    ui = {"type": "page", "children": [
        {"type": "tabs", "children": [
            {"type": "tab", "label": "甲", "children": [{"type": "chart", "chart": "c1"}]},
            {"type": "tab", "label": "乙", "children": [{"type": "chart", "chart": "c2"}]}]},
        {"type": "markdown", "text": "**重点**：看这里"},
    ]}
    body = render_body(ui, charts)
    assert "dbaide-tabs" in body and 'data-tab="0-0"' in body and "甲" in body and "乙" in body
    assert "dbaide-tabpanel" in body
    assert "<strong>重点</strong>" in body            # mini-markdown applied


def test_markdown_tile_renders_pipe_table():
    charts = [_chart("c1")]
    md = "## 关键发现\n\n| 城市 | 销售额 |\n|---|---|\n| 广州 | **15808** |\n| 成都 | 15241 |\n\n说明文字"
    body = render_body({"type": "page", "children": [
        {"type": "markdown", "text": md}, {"type": "chart", "chart": "c1"}]}, charts)
    assert "dbaide-md-table" in body                       # the pipe table became an HTML table
    assert "<th>城市</th>" in body and "<th>销售额</th>" in body
    assert "<td>广州</td>" in body and "<strong>15808</strong>" in body   # inline markdown inside cells
    assert "|---|" not in body and "| 城市 |" not in body  # raw pipe syntax not leaked
    assert "<h3>关键发现</h3>" in body and "说明文字" in body


def test_render_tree_grid_and_nesting():
    charts = [_chart("c1"), _chart("c2"), _chart("c3")]
    ui = {"type": "grid", "cols": 3, "children": [
        {"type": "chart", "chart": "c1"}, {"type": "chart", "chart": "c2"}, {"type": "chart", "chart": "c3"}]}
    body = render_body(ui, charts)
    assert "repeat(3,1fr)" in body and body.count('data-kind="chart"') == 3


def test_unknown_container_passes_through_children():
    charts = [_chart("c1")]
    ui = {"type": "page", "children": [
        {"type": "mystery", "children": [{"type": "chart", "chart": "c1"}]}]}   # unknown → render kids
    assert 'data-chart="c1"' in render_body(ui, charts)


def test_bad_chart_ref_dropped_and_uncovered_appended():
    charts = [_chart("c1"), _chart("c2")]
    ui = {"type": "page", "children": [
        {"type": "chart", "chart": "ghost"},     # dropped
        {"type": "chart", "chart": "c1"}]}       # c2 unplaced → appended
    body = render_body(ui, charts)
    assert "ghost" not in body and 'data-chart="c1"' in body and 'data-chart="c2"' in body


def test_empty_or_garbage_tree_falls_back_to_auto_grid():
    charts = [_chart("c1")]
    assert "dbaide-grid" in render_body(None, charts)
    assert "dbaide-grid" in render_body({"type": "page", "children": []}, charts)


def test_legacy_rows_still_render():
    charts = [_chart("c1")]
    legacy = [{"tiles": [{"kind": "chart", "chart": "c1", "span": 12}]}]   # old saved schema
    assert 'data-chart="c1"' in render_body(legacy, charts) and "dbaide-row" in render_body(legacy, charts)


def test_dynamic_default_resolved_in_control():
    import re
    charts = [_chart("c1", [ParamSpec("start", "date", default="@month_start")])]
    body = render_body({"type": "chart", "chart": "c1"}, charts)
    m = re.search(r'data-param="start"[^>]*value="([^"]*)"', body)
    # the @token is resolved to a concrete date so the initial filter is visible/editable
    assert m and re.match(r"\d{4}-\d{2}-\d{2}$", m.group(1)) and "@month_start" not in body


def test_page_uses_async_bridge_and_loading():
    from dbaide.rendering.dashboard_page import build_dashboard_page
    page = build_dashboard_page("<div></div>", echarts_src="x", theme={})
    assert "bridge.request(" in page and "resultReady.connect" in page   # async, off-thread queries
    assert "markLoading" in page and "dbaide-spin" in page               # per-tile loading view
    assert "showBusy" in page and "dbaide-busy" in page                  # overlay spinner on apply/refresh
    assert "fallbackChart" in page                                       # client-side chart from rows when spec is null


def test_kpi_tile_carries_format_and_trend():
    charts = [_chart("c1")]
    ui = {"type": "page", "children": [
        {"type": "kpi", "chart": "c1", "label": "总额", "format": "currency", "trend": True}]}
    body = render_body(ui, charts)
    # data-chart/kind/format/trend live on the card; value+spark slots present
    assert 'data-kind="kpi"' in body and 'data-format="currency"' in body and 'data-trend="1"' in body
    assert "dbaide-kpi-value" in body and "dbaide-kpi-spark" in body
    # a plain KPI has empty trend flag
    plain = render_body({"type": "kpi", "chart": "c1", "label": "x"}, charts)
    assert 'data-trend=""' in plain


def test_multiselect_has_select_all_and_clear():
    charts = [_chart("c1", [ParamSpec("region", "enum", options=["A", "B"], multi=True)])]
    body = render_body({"type": "chart", "chart": "c1"}, charts)
    assert "data-ckall" in body and "data-ckno" in body and "dbaide-ckbar" in body


def test_controls_auto_generated_and_deduped():
    charts = [_chart("c1", [ParamSpec("region", "enum", options=["A", "B"], multi=True, default=["A"])]),
              _chart("c2", [ParamSpec("region", "enum", options=["A", "B"], multi=True)])]
    ui = {"type": "row", "children": [{"type": "chart", "chart": "c1"}, {"type": "chart", "chart": "c2"}]}
    body = render_body(ui, charts)
    assert body.count("dbaide-dd") == 1 and "data-apply" in body
    assert 'type="checkbox" data-param="region" value="A" checked' in body
    assert render_controls([_chart("c1", [])]) == ""        # no params → no control bar
