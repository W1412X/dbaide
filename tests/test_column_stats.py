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


def test_explicit_top_values_honored_on_text_column(tmp_path):
    """A char/text flag column must return top_values when explicitly asked — the
    type whitelist only governs the default set, not explicit picks."""
    pt = _tools(tmp_path)
    stats = pt.column_stats("t", ["status"], metrics=["distinct_count", "top_values"])[0]["stats"]
    assert stats["distinct_count"] == 2
    vals = {tv["value"]: tv["count"] for tv in stats["top_values"]}
    assert vals == {"paid": 2, "pending": 1}


def test_unsupported_metric_gets_a_note(tmp_path):
    pt = _tools(tmp_path)
    stats = pt.column_stats("t", ["status"], metrics=["nonsense"])[0]["stats"]
    assert "note" in stats and "nonsense" in stats["note"]


def test_scalar_aggregates_share_one_table_scan(tmp_path):
    """Every column's scalar aggregates are computed in ONE scan, not one per column;
    top_values (a GROUP BY) stays per-column."""
    pt = _tools(tmp_path)
    calls: list[str] = []
    orig = pt.adapter.execute_readonly
    pt.adapter.execute_readonly = lambda sql, **kw: (calls.append(sql), orig(sql, **kw))[1]

    pt.column_stats("t")  # 4 columns, scalar defaults only
    assert len([s for s in calls if "GROUP BY" not in s]) == 1  # single aggregate scan

    calls.clear()
    pt.column_stats("t", ["status", "note"], metrics=["distinct_count", "top_values"])
    assert len([s for s in calls if "GROUP BY" not in s]) == 1   # one shared scalar scan
    assert len([s for s in calls if "GROUP BY" in s]) == 2       # one top_values per column


def test_batch_falls_back_per_column_on_query_error(tmp_path):
    """If the combined scalar query fails, results are still computed per column so one
    incompatible column can't wipe out the rest."""
    pt = _tools(tmp_path)
    orig = pt.adapter.execute_readonly
    state = {"first": True}

    def flaky(sql, **kw):
        # Fail only the first (batched, multi-column) aggregate scan; let per-column retries through.
        if state["first"] and "GROUP BY" not in sql and sql.count(" AS m") > 1:
            state["first"] = False
            raise RuntimeError("simulated batch failure")
        return orig(sql, **kw)

    pt.adapter.execute_readonly = flaky
    by_col = {s["column"]: s for s in pt.column_stats("t")}
    assert abs(by_col["amount"]["stats"]["null_rate"] - 0.3333) < 0.001  # recovered per-column
    assert by_col["note"]["stats"].get("empty_rate", 0) > 0


def _scoped_tools(tmp_path, *, deny=None, allow=None):
    """Build a sqlite DB with assets so the db ('main') resolves, then return scoped
    SchemaTools + ProfileTools sharing it."""
    import sqlite3
    from dbaide.adapters import build_adapter
    from dbaide.assets import AssetBuilder
    from dbaide.joins import JoinCatalogStore
    from dbaide.tools.schema import SchemaTools

    db = tmp_path / "scope.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, a REAL); INSERT INTO t VALUES (1,2.0);")
    c.commit(); c.close()
    conn = ConnectionConfig(name="sc", type="sqlite", path=str(db),
                            table_deny=list(deny or []), table_allow=list(allow or []))
    store = AssetStore(tmp_path / "a")
    jc = JoinCatalogStore(base_dir=tmp_path / "j")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store, join_catalog=jc).build(
        profile_mode="none", sample=False,
    )
    st = SchemaTools(build_adapter(conn), DisclosureContext(), instance="sc", assets=store)
    pt = ProfileTools(build_adapter(conn), DisclosureContext(), instance="sc", assets=store)
    return st, pt


def test_qualified_deny_not_bypassed_by_bare_table_name(tmp_path):
    """A database-qualified deny rule ('main.t') must block a BARE describe/profile call:
    the tool resolves the database before the scope check, so the rule still matches."""
    import pytest
    st, pt = _scoped_tools(tmp_path, deny=["main.t"])
    for call in (
        lambda: st.describe_table("t"),
        lambda: st.foreign_keys("t"),
        lambda: pt.sample_rows("t"),
        lambda: pt.column_stats("t"),
        lambda: pt.profile_table("t"),
    ):
        with pytest.raises(PermissionError):
            call()


def test_qualified_allow_not_overblocked_for_bare_table_name(tmp_path):
    """A qualified allow rule ('main.t') must permit a BARE describe call — resolving the
    database first means the allowed table isn't wrongly treated as out-of-scope."""
    st, _ = _scoped_tools(tmp_path, allow=["main.t"])
    cols = st.describe_table("t")
    assert {c.name for c in cols} == {"id", "a"}
