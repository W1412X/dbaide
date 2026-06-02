"""On-demand, type-aware column statistics (replaces offline per-column profiling)."""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.assets import AssetStore
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ConnectionConfig
from dbaide.tools.profile import ProfileTools


def _tools(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, amount REAL, status TEXT, note TEXT);"
        "INSERT INTO t VALUES (1,10.5,'paid','hi'),(2,20.0,'pending',''),(3,NULL,'paid','xx');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="s", type="sqlite", path=str(db))
    return ProfileTools(build_adapter(conn), DisclosureContext(), instance="s",
                        assets=AssetStore(tmp_path / "a"))


def test_type_aware_defaults(tmp_path):
    pt = _tools(tmp_path)
    by_col = {s["column"]: s for s in pt.column_stats("t")}
    # numeric → min/max/null_rate; null_rate reflects the 1/3 NULL amount
    assert by_col["amount"]["kind"] == "numeric"
    assert set(by_col["amount"]["stats"]) == {"min", "max", "null_rate"}
    assert abs(by_col["amount"]["stats"]["null_rate"] - 0.3333) < 0.001
    # string → length + empty_rate (note is 1/3 empty)
    assert by_col["note"]["kind"] == "text"
    assert by_col["note"]["stats"]["empty_rate"] > 0
    assert "min_len" in by_col["note"]["stats"]


def test_llm_picks_metrics(tmp_path):
    pt = _tools(tmp_path)
    stats = pt.column_stats("t", ["amount"], metrics=["min", "max", "distinct_count"])[0]["stats"]
    assert set(stats) == {"min", "max", "distinct_count"}
    assert stats["distinct_count"] == 2


def test_tool_registered_and_exposed_to_loop():
    from dbaide.agent.toolkit import LOOP_DECISION_TOOL_NAMES
    assert "column_stats" in LOOP_DECISION_TOOL_NAMES
