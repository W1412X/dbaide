"""Param-type coverage: multi-select, dynamic dates, ranges, expression filters."""

from __future__ import annotations

from datetime import date

import pytest

from dbaide.boards.dates import resolve_dynamic, resolve_value
from dbaide.boards.parametric import ParamSpec
from dbaide.boards.runtime import render_sql

REF = date(2024, 3, 15)   # a fixed "today" for deterministic tests


# -- dynamic date helpers ---------------------------------------------------

def test_resolve_dynamic_tokens():
    assert resolve_dynamic("@today", REF) == "2024-03-15"
    assert resolve_dynamic("@yesterday", REF) == "2024-03-14"
    assert resolve_dynamic("@month_start", REF) == "2024-03-01"
    assert resolve_dynamic("@year_start", REF) == "2024-01-01"
    assert resolve_dynamic("@quarter_start", REF) == "2024-01-01"
    assert resolve_dynamic("@days_ago:30", REF) == "2024-02-14"
    assert resolve_dynamic("@months_ago:2", REF) == "2024-01-01"
    assert resolve_dynamic("@year", REF) == 2024
    assert resolve_dynamic("@month", REF) == 3
    assert resolve_dynamic("@month_str", REF) == "2024-03"
    assert resolve_dynamic("not a token", REF) is None
    assert resolve_value("literal", REF) == "literal"           # passthrough


# -- multi-select → IN list -------------------------------------------------

def test_multi_select_expands_to_in_list():
    p = ParamSpec("cats", "enum", options=["A", "B", "C"], multi=True)
    out = render_sql("WHERE cat IN (:cats)", {"cats": ["A", "C"]}, [p])
    assert out == "WHERE cat IN ('A', 'C')"


def test_multi_select_validates_each_value():
    p = ParamSpec("cats", "enum", options=["A", "B"], multi=True)
    with pytest.raises(ValueError):
        render_sql("cat IN (:cats)", {"cats": ["A", "Z"]}, [p])   # Z not allowed


def test_multi_number_no_quotes():
    p = ParamSpec("ids", "number", multi=True)
    assert render_sql("id IN (:ids)", {"ids": [1, 2, 3]}, [p]) == "id IN (1, 2, 3)"


# -- dynamic default resolved at bind time ----------------------------------

def test_dynamic_default_resolves_when_value_absent():
    p = ParamSpec("start", "date", default="@days_ago:7")
    out = render_sql("WHERE d >= :start", {}, [p], today=REF)   # no value → dynamic default
    assert out == "WHERE d >= '2024-03-08'"


def test_dynamic_value_from_control():
    p = ParamSpec("d", "date", default="2024-01-01")
    out = render_sql("WHERE day = :d", {"d": "@today"}, [p], today=REF)
    assert out == "WHERE day = '2024-03-15'"


# -- range = two single params ----------------------------------------------

def test_range_is_two_params():
    params = [ParamSpec("start", "date", default="@month_start"), ParamSpec("end", "date", default="@today")]
    out = render_sql("WHERE d BETWEEN :start AND :end", {}, params, today=REF)
    assert out == "WHERE d BETWEEN '2024-03-01' AND '2024-03-15'"


# -- filter on an EXPRESSION of a column, not the raw column -----------------

def test_param_binds_inside_an_expression():
    out = render_sql("WHERE YEAR(order_date) = :y AND lower(name) LIKE :kw",
                     {"y": 2024, "kw": "%acme%"},
                     [ParamSpec("y", "number"), ParamSpec("kw", "text")])
    assert "YEAR(order_date) = 2024" in out
    assert "lower(name) LIKE '%acme%'" in out
