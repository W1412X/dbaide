"""The body-normalization layer keeps an AI dashboard renderable regardless of
what the builder emits (missing/garbled HTML, injected scripts, missing apply)."""

from __future__ import annotations

from dbaide.boards.parametric import Combine, ParametricChart, ParamSpec, QuerySource
from dbaide.rendering.dashboard_body import (
    chart_container_ids,
    default_body,
    normalize_body,
    strip_scripts,
)


def _chart(cid="c1", params=None):
    return ParametricChart(
        chart_id=cid, title=f"图 {cid}",
        sources=[QuerySource("s", "SELECT a, b FROM s WHERE m=:m")],
        params=params if params is not None else [ParamSpec("m", "date", default="@month_str")],
        combine=Combine("single"),
        chart_plan={"chart_type": "bar", "category_field": "a", "value_fields": ["b"]},
    )


def test_strip_scripts_removes_injected_js():
    assert strip_scripts('<div>ok</div><script>alert(1)</script>') == '<div>ok</div>'
    assert strip_scripts('<SCRIPT src="x.js">\n more \n</script>x') == 'x'


def test_default_body_has_a_container_for_every_chart():
    body = default_body([_chart("c1"), _chart("c2")])
    assert chart_container_ids(body) == {"c1", "c2"}
    assert "data-apply" in body and "dbaide-card" in body


def test_default_body_renders_a_control_per_unique_param():
    charts = [_chart("c1", [ParamSpec("region", "enum", options=["A", "B"], multi=True)]),
              _chart("c2", [ParamSpec("region", "enum", options=["A", "B"], multi=True),
                            ParamSpec("n", "number", default=5)])]
    body = default_body(charts)
    assert body.count('data-param="region"') == 1            # deduped across charts
    assert 'data-param="n"' in body and 'value="5"' in body  # literal default rendered
    assert "<select" in body and "multiple" in body          # multi enum → multi-select


def test_enum_defaults_are_preselected():
    # defaults must be reflected as selected <option>s, else collectParams sends nothing on load
    multi = default_body([_chart("c1", [ParamSpec("r", "enum", options=["华东", "华北"],
                                                  multi=True, default=["华东"])])])
    assert '<option value="华东" selected>' in multi and '<option value="华北">' in multi
    single = default_body([_chart("c2", [ParamSpec("r", "enum", options=["A", "B"], default="B")])])
    assert '<option value="B" selected>' in single


def test_at_token_default_leaves_control_empty():
    body = default_body([_chart("c1", [ParamSpec("m", "date", default="@month_start")])])
    assert 'data-param="m"' in body and "@month" not in body   # @token never leaks into value


def test_normalize_keeps_a_good_model_body():
    charts = [_chart("c1")]
    good = '<div class="dbaide-controls"><button data-apply>Go</button></div>' \
           '<div data-chart="c1" style="height:280px"></div>'
    assert normalize_body(good, charts) == good                # usable body is preserved


def test_normalize_falls_back_when_a_chart_has_no_container():
    charts = [_chart("c1"), _chart("c2")]
    partial = '<div data-chart="c1"></div>'                    # c2 missing → not trustworthy
    out = normalize_body(partial, charts)
    assert chart_container_ids(out) == {"c1", "c2"}            # regenerated to cover all


def test_normalize_falls_back_on_empty_or_garbage():
    charts = [_chart("c1")]
    assert chart_container_ids(normalize_body("", charts)) == {"c1"}
    assert chart_container_ids(normalize_body("just some text", charts)) == {"c1"}


def test_normalize_strips_scripts_and_injects_apply_when_filters_exist():
    charts = [_chart("c1")]
    body = '<script>evil()</script><div data-chart="c1"></div>'   # has filters, no apply button
    out = normalize_body(body, charts)
    assert "<script" not in out.lower()
    assert "data-apply" in out                                   # apply trigger added so filters work
