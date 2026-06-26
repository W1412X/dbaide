"""Regression tests for issues found during the round-3 codebase audit.
One test (or small group) per fix; see the matching commit for context."""

from __future__ import annotations

from dbaide.agent.chart_agent import _materialize_gauge, chart_plan_from_dict


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
