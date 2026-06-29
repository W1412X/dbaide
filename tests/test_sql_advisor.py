"""Unit tests for the advisory SQL optimizer (suggestions only, no rewrite)."""

from __future__ import annotations

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ConnectionConfig
from dbaide.tools import QueryTools
from dbaide.tools.sql_advisor import SqlAdvisor


def _qt(tmp_path):
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT, amount REAL)")
    con.executemany("INSERT INTO t(name, amount) VALUES(?, ?)", [("x", 1.0), ("y", 2.0)])
    con.commit()
    con.close()
    return QueryTools(build_adapter(ConnectionConfig(name="a", type="sqlite", path=str(db))),
                      DisclosureContext())


def test_advisor_flags_cost_scan_select_star_and_non_sargable(tmp_path):
    advice = SqlAdvisor(_qt(tmp_path)).advise(
        "SELECT * FROM t WHERE LOWER(name) = 'x'", estimated_rows=3_200_000)
    assert advice is not None
    assert "3,200,000" in advice                 # cost callout
    assert "SELECT *" in advice                   # projection lint
    assert "non-sargable" in advice               # function wraps the filtered column
    assert "scan" in advice.lower()               # SQLite full scan on t (no index on name)


def test_advisor_flags_leading_wildcard(tmp_path):
    advice = SqlAdvisor(_qt(tmp_path)).advise(
        "SELECT id, name FROM t WHERE name LIKE '%abc'", estimated_rows=2_000_000)
    assert advice is not None and "leading wildcard" in advice


def test_advisor_returns_none_when_nothing_actionable(tmp_path):
    # explicit columns, indexed PK filter (SQLite uses the integer PK — no scan), no lints
    advice = SqlAdvisor(_qt(tmp_path)).advise(
        "SELECT id, name FROM t WHERE id = 5", estimated_rows=1_500_000)
    assert advice is None


def test_advisor_does_not_flag_count_star_as_select_star(tmp_path):
    advice = SqlAdvisor(_qt(tmp_path)).advise(
        "SELECT COUNT(*) FROM t WHERE id = 5", estimated_rows=1_500_000)
    # COUNT(*) is not SELECT *, and the PK filter avoids a scan → nothing actionable
    assert advice is None
