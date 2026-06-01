"""Regression tests for bugs found in the comprehensive audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dbaide.adapters.base import append_limit, outer_limit_value
from dbaide.agent.runtime import AgentRuntime
from dbaide.config import ConfigManager, _toml_quote
from dbaide.models import ConnectionConfig
from dbaide.tools.profile import ProfileTools
from dbaide.tools.registry import ToolResult
from dbaide.validation.sql_guard import SQLGuard


# 1) CRITICAL: agent loop step budget actually decrements.
class _FakeRegistry:
    def invoke(self, name, args, ctx):
        return ToolResult(ok=True, data={})


def test_call_tool_consumes_step_budget():
    rt = AgentRuntime(tool_registry=_FakeRegistry())
    assert rt.steps_remaining == AgentRuntime.MAX_STEPS
    for _ in range(AgentRuntime.MAX_STEPS):
        rt.call_tool("x", {}, None)
    assert rt.steps_remaining == 0
    with pytest.raises(RuntimeError):
        rt.call_tool("x", {}, None)  # budget exhausted → loop terminates


# 2) HIGH: LIMIT detection is top-level only; offset form parsed correctly.
def test_limit_bypass_and_offset():
    assert outer_limit_value("SELECT * FROM (SELECT x FROM big LIMIT 5) q") is None
    assert "LIMIT 100" in append_limit("SELECT * FROM (SELECT x LIMIT 5) q", 100)
    assert outer_limit_value("SELECT * FROM t WHERE n = 'limit 5'") is None
    assert outer_limit_value("SELECT * FROM t LIMIT 10") == 10
    assert outer_limit_value("SELECT * FROM t LIMIT 0, 999999") == 999999  # offset,count
    g = SQLGuard(default_limit=100, max_row_limit=1000)
    assert g.validate("SELECT * FROM t LIMIT 0, 999999").ok is False  # cap not bypassed
    assert g.validate("SELECT id FROM t LIMIT 0, 50").ok is True


# 3) HIGH: config survives control characters instead of being wiped.
def test_config_survives_control_chars(tmp_path: Path):
    import tomllib
    assert tomllib.loads(f"x = {_toml_quote('a' + chr(10) + 'b')}")["x"] == "a\nb"
    cfg = ConfigManager(path=tmp_path / "c.toml")
    cfg.upsert_connection(ConnectionConfig(name="c1", type="sqlite", path="/x.db", password="a\nb\tc"))
    reloaded = ConfigManager(path=tmp_path / "c.toml")
    assert "c1" in reloaded.connections()  # not wiped
    assert reloaded.connections()["c1"].password == "a\nb\tc"


# 4) HIGH: offline profile cache reads the persisted 'statistics' shape.
def test_profile_cache_reconstructs_from_statistics():
    doc = {
        "name": "amount", "profile_status": "profiled",
        "statistics": {"data_kind": "numeric", "row_count": 100, "null_count": 2,
                       "distinct_count": 90, "min_value": 1, "max_value": 9,
                       "numeric_stats": {"avg": 5}},
        "top_values": [{"value": 1, "count": 3}], "sample_values": [1, 2, 3],
    }
    profile = ProfileTools._profile_from_doc("orders", "amount", doc)
    assert profile is not None
    assert profile.row_count == 100 and profile.distinct_count == 90
    assert profile.numeric_stats == {"avg": 5}
    assert profile.sample_values == [1, 2, 3]
    # not-profiled docs don't reconstruct (forces a live scan)
    assert ProfileTools._profile_from_doc("orders", "x", {"profile_status": "not_profiled"}) is None


# 5) MEDIUM: join max_left_per_right is not inflated by duplicate right keys.
def test_join_cardinality_no_product_inflation(tmp_path: Path):
    from dbaide.adapters import build_adapter
    from dbaide.agent.join_validation import JoinSampleValidator
    from dbaide.agent.orchestrator import AskOrchestrator
    from dbaide.llm import LLMClient
    from dbaide.session import Session

    class _M(LLMClient):
        def complete_json(self, m, *, schema_hint=""):
            return {}
        def complete_text(self, m):
            return "ok"

    db = tmp_path / "j.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE l(v INT); INSERT INTO l VALUES (1),(1),(1);"
                    "CREATE TABLE r(k INT); INSERT INTO r VALUES (1),(1);")
    c.commit()
    c.close()
    conn = ConnectionConfig(name="j", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), _M())
    stats = JoinSampleValidator(orch, sample_size=50)._sample_stats("l", "v", "r", "k", database="")
    assert stats["max_left_per_right"] == 3   # 3 left rows map to key 1 (was 6 = 3×2 before fix)
    assert stats["max_right_per_left"] == 2


# 6) MEDIUM: ordered lists are closed with </ol>, not </ul>.
def test_ordered_list_closes_with_ol():
    from dbaide.rendering.markdown import _process_blocks
    out = _process_blocks("1. a\n2. b\n\nafter")
    assert "<ol>" in out and "</ol>" in out and "</ul>" not in out
    out2 = _process_blocks("- a\n- b\n\nx")
    assert "<ul>" in out2 and "</ul>" in out2 and "</ol>" not in out2


# 7) LOW: a failed run marks the in-flight node failed, not completed.
def test_finalize_failed_marks_running_node_failed():
    from dbaide.agent.progress_events import progress_event
    from dbaide.agent.trace_model import TraceModel
    m = TraceModel()
    m.ingest(progress_event(stage="execute_sql", title="x", status="running", kind="tool", step=1), now=1.0)
    m.finalize(failed=True)
    assert m.overall == "failed"
    assert m.steps[0].status == "failed"


# 8) LOW: highlight_sql no longer raises NameError and highlights strings.
def test_highlight_sql_runs():
    from dbaide.rendering.sanitize import highlight_sql
    out = highlight_sql("SELECT * FROM t WHERE name = 'bob' AND n = 5")
    assert "color:#22863a" in out  # string span, no NameError


# 9) loop charges budget for unknown tools (no infinite loop) + consume_step.
def test_consume_step_charges_budget():
    rt = AgentRuntime(tool_registry=_FakeRegistry())
    before = rt.steps_remaining
    rt.consume_step()
    assert rt.steps_remaining == before - 1


# 10) execute_readonly_sql is treated as an execute tool by the loop.
def test_execute_alias_recognized():
    from dbaide.agent.loop import _EXECUTE_TOOLS
    assert "execute_sql" in _EXECUTE_TOOLS
    assert "execute_readonly_sql" in _EXECUTE_TOOLS


# 11) sqlite row estimate via dbstat uses valid columns (no error) when ANALYZE absent.
def test_sqlite_estimate_rows_dbstat(tmp_path: Path):
    from dbaide.adapters import build_adapter
    db = tmp_path / "e.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);"
                    + "".join(f"INSERT INTO t VALUES ({i},'x{i}');" for i in range(50)))
    c.commit()
    c.close()
    adapter = build_adapter(ConnectionConfig(name="e", type="sqlite", path=str(db)))
    tables = {t.name: t for t in adapter.list_tables()}
    # estimated_rows should be a non-negative int or None — never raise.
    est = tables["t"].estimated_rows
    assert est is None or est >= 0
