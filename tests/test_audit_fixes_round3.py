"""Regression tests for issues found during the round-3 codebase audit.
One test (or small group) per fix; see the matching commit for context."""

from __future__ import annotations

import pytest

from dbaide.agent.chart_agent import (
    _materialize,
    _materialize_boxplot,
    _materialize_gauge,
    _materialize_heatmap,
    _materialize_sankey,
    chart_plan_from_dict,
)
from dbaide.charts.spec import CHART_TYPES
from dbaide.cli import _print_backup_result


@pytest.mark.parametrize("chart_type", sorted(CHART_TYPES))
@pytest.mark.parametrize("rows", [[], [{}], [{"a": 1, "b": "x"}]], ids=["empty", "one-empty", "one-row"])
def test_every_chart_type_survives_degenerate_input(chart_type, rows):
    # No chart type may crash on an empty result set or a plan missing its role
    # fields — a tile must degrade to an empty chart, never raise.
    plan = chart_plan_from_dict({"chart_type": chart_type})  # minimal plan, no fields
    out = _materialize(plan, rows)
    assert isinstance(out, dict) and "data" in out


def test_gauge_chart_handles_empty_rows():
    # A gauge query returning zero rows must not crash (rows[0] / value_fields[0]).
    plan = chart_plan_from_dict({"chart_type": "gauge", "value_fields": ["amount"]})
    out = _materialize_gauge(plan, [])
    assert out["data"]["value"] == 0.0
    assert "name" in out["data"]


def test_gauge_chart_handles_missing_value_field():
    plan = chart_plan_from_dict({"chart_type": "gauge", "value_fields": []})
    out = _materialize_gauge(plan, [{"x": 1}])
    assert out["data"]["value"] == 0.0


def test_parametric_from_dict_tolerates_missing_required_fields():
    # The dashboard builder feeds model JSON straight through ParametricChart.from_dict;
    # a source missing id/sql or a param missing name must not crash the build with
    # TypeError — it should construct (and an empty sql then fails validation, which the
    # builder can repair).
    from dbaide.boards.parametric import ParametricChart
    chart = ParametricChart.from_dict({
        "chart_id": "c1",
        "sources": [{"sql": "SELECT 1"}, {"id": "s2"}],  # first lacks id, second lacks sql
        "params": [{}, {"name": "region", "type": "enum"}],  # first lacks name
        "chart_plan": {"chart_type": "bar"},
    })
    assert len(chart.sources) == 2
    assert chart.sources[0].id == "" and chart.sources[1].sql == ""
    assert chart.params[0].name == "" and chart.params[1].name == "region"


def test_import_manifest_save_is_atomic_and_round_trips(tmp_path):
    from dbaide.ingest.manifest import ImportManifest
    m = ImportManifest(version=1, workbooks=[])
    path = tmp_path / "sub" / "manifest.json"  # parent doesn't exist yet
    m.save(path)
    assert path.exists()
    # no temp files left behind
    assert not list(path.parent.glob("*.tmp"))
    loaded = ImportManifest.load(path)
    assert loaded.to_dict() == m.to_dict()


def test_progressive_schema_assets_handles_unnamed_database(monkeypatch):
    # _discover_from_assets filters out unnamed databases when building db_items but the
    # kept indices are positions in the ORIGINAL list; indexing the filtered db_items
    # with them crashed (IndexError) when an unnamed database preceded a kept one.
    from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
    from dbaide.llm import LLMClient

    class _LLM(LLMClient):  # any non-null client; the restrict path never calls it
        pass

    class FakeStore:
        def has_instance(self, inst, *, fingerprint=""):
            return True

        def database_docs(self, inst, *, fingerprint=""):
            return [{"name": "db1"}, {"name": ""}, {"name": "db2"}]  # unnamed in the middle

        def table_docs(self, inst, db, *, fingerprint=""):
            return []

        def column_docs(self, inst, db, tbl, *, fingerprint=""):
            return []

    agent = ProgressiveSchemaAgent(_LLM(), FakeStore(), "inst")
    monkeypatch.setattr(agent, "_asset_fingerprint", lambda: "")
    # restrict to db2 → kept index is 2 (its original position); the filtered db_items
    # has only 2 entries, so the old code raised IndexError here.
    res = agent._discover_from_assets("q", restrict_databases={"db2"})
    names = {h.name for h in res.hits}
    assert "db2" in names      # correctly resolved + scanned
    assert "" not in names     # the unnamed database is never scanned


def test_mcp_tool_context_get_is_thread_safe(monkeypatch):
    # MCP runs each ask on its own thread; concurrent get() for one connection must
    # build the adapter exactly once (no unlocked check-then-act race).
    import threading
    import time
    from dbaide.mcp_server import _ToolContext

    ctx = _ToolContext()
    calls = {"n": 0}

    def fake_build(conn_name):
        calls["n"] += 1
        time.sleep(0.05)  # widen the race window
        return ("adapter", "s", "q", "p")

    monkeypatch.setattr(ctx, "_build", fake_build)
    monkeypatch.setattr(ctx, "_connection_hash", lambda c: "h")
    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        results.append(ctx.get("c"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1  # built once despite 8 concurrent gets
    assert all(r == ("adapter", "s", "q", "p") for r in results)


def test_join_catalog_add_many_matches_repeated_add(tmp_path):
    # add_many must produce the same catalog as calling add() per relation (same
    # undirected dedup), in one load+save instead of O(n) of them.
    from dbaide.joins.catalog import JoinCatalogStore
    rels = [
        {"table": "orders", "column": "user_id", "ref_table": "users", "ref_column": "id"},
        {"table": "users", "column": "id", "ref_table": "orders", "ref_column": "user_id"},  # reverse dup
        {"table": "orders", "column": "product_id", "ref_table": "products", "ref_column": "id"},
    ]
    fp = "fp1"
    a = JoinCatalogStore(tmp_path / "a")
    for r in rels:
        a.add("local", r, source="foreign_key", database="", fingerprint=fp)
    b = JoinCatalogStore(tmp_path / "b")
    n = b.add_many("local", rels, source="foreign_key", fingerprint=fp)

    def edges(recs):
        return sorted(tuple(sorted([(x["table"], x["column"]), (x["ref_table"], x["ref_column"])])) for x in recs)

    ra, rb = a._load("local"), b._load("local")
    assert len(ra) == len(rb) == 2  # the reverse duplicate is merged
    assert edges(ra) == edges(rb)
    assert n == 3  # three relations processed


def test_heatmap_axes_are_deterministic_regardless_of_row_order():
    # Same data in two row orders must yield the same axes + cell mapping (the cell
    # coordinates are stable across refreshes).
    plan = chart_plan_from_dict(
        {"chart_type": "heatmap", "x_field": "x", "y_field": "y", "value_fields": ["v"]})
    rows_a = [{"x": "b", "y": "2", "v": 1}, {"x": "a", "y": "1", "v": 2}, {"x": "a", "y": "2", "v": 3}]
    rows_b = list(reversed(rows_a))
    a = _materialize_heatmap(plan, rows_a)["data"]
    b = _materialize_heatmap(plan, rows_b)["data"]
    assert a["x_categories"] == b["x_categories"] == ["a", "b"]
    assert a["y_categories"] == b["y_categories"] == ["1", "2"]
    assert sorted(map(tuple, a["points"])) == sorted(map(tuple, b["points"]))


def test_render_body_is_always_safe_with_none_charts():
    # render_body documents "always safe"; charts=None must not crash (a board may
    # have no charts).
    from dbaide.rendering.dashboard_body import render_body
    for layout in (None, {}, {"type": "row", "children": [{"type": "chart", "chart_id": "x"}]}):
        out = render_body(layout, None)
        assert isinstance(out, str)


def test_special_charts_tolerate_missing_value_field():
    # heatmap/sankey/boxplot indexed value_fields[0] unguarded → crash on a plan with
    # no value field. They must degrade (zeros), not raise.
    rows = [{"x": "a", "y": "b", "src": "a", "tgt": "b", "cat": "g", "v": 1}]
    hm = _materialize_heatmap(
        chart_plan_from_dict({"chart_type": "heatmap", "x_field": "x", "y_field": "y", "value_fields": []}), rows)
    assert "points" in hm["data"]
    sk = _materialize_sankey(
        chart_plan_from_dict({"chart_type": "sankey", "source_field": "src", "target_field": "tgt", "value_fields": []}), rows)
    assert "data" in sk
    bx = _materialize_boxplot(
        chart_plan_from_dict({"chart_type": "boxplot", "category_field": "cat", "value_fields": []}), rows)
    assert "data" in bx


def test_print_backup_result_tolerates_partial_dict(capsys):
    # A success result missing fields must not KeyError-crash the CLI output.
    _print_backup_result({"file_size": 10})  # no database/table/row_count/file_path
    out = capsys.readouterr().out
    assert "OK" in out and "rows" in out


def test_dashboard_page_esc_escapes_quotes():
    # esc() writes column names into data-col="..." attributes; a column name with a
    # double quote must not break out of the attribute (injection).
    from dbaide.rendering.dashboard_page import build_dashboard_page
    page = build_dashboard_page("<div></div>", echarts_src="echarts.js")
    assert "'\"':'&quot;'" in page  # esc map covers the double quote
    assert '/[&<>"\']/g' in page    # and the regex matches it
