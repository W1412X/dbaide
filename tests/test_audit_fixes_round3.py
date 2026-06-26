"""Regression tests for issues found during the round-3 codebase audit.
One test (or small group) per fix; see the matching commit for context."""

from __future__ import annotations

from dbaide.agent.chart_agent import _materialize_gauge, chart_plan_from_dict
from dbaide.cli import _print_backup_result


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
