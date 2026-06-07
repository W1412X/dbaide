"""Tests for the agent SQL hard gates: max LIMIT, unfiltered SELECT *, EXPLAIN cost, joins."""

from __future__ import annotations

from dbaide.core.result import ExecutionPolicy, ValidationReport
from dbaide.agent.controllers import RiskController
from dbaide.agent.loop import _risk_reply_confirms
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.adapters import build_adapter
from dbaide.llm import NullLLMClient
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext
from dbaide.validation.sql_guard import SQLGuard


class TestSQLGuardHardCaps:
    def test_limit_above_max_requires_confirmation(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        result = guard.validate("SELECT * FROM t LIMIT 50000")
        assert result.ok is True
        report = guard.validate_with_report("SELECT * FROM t LIMIT 50000")
        assert report.risk_level == "high"
        assert report.requires_confirmation is True
        assert any("LIMIT 50000" in w for w in report.warnings)

    def test_limit_within_max_ok(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        assert guard.validate("SELECT id FROM t LIMIT 500").ok is True

    def test_unfiltered_select_star_is_capped(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        result = guard.validate("SELECT * FROM users")
        assert result.ok is True
        assert "limit 100" in result.normalized_sql.lower()

    def test_many_joins_not_limited_by_count_alone(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        sql = "SELECT * FROM a JOIN b ON a.id=b.a JOIN c ON c.b=b.id JOIN d ON d.c=c.id WHERE a.x=1"
        report = guard.validate_with_report(sql)
        assert report.risk_level != "high"
        assert report.requires_confirmation is False


class TestRiskControllerHardGates:
    def _ok_report(self):
        return ValidationReport(ok=True, normalized_sql="SELECT 1", issues=[], warnings=[],
                                risk_level="low", requires_confirmation=False)

    def test_expert_still_blocked_by_explain_cost(self):
        rc = RiskController()
        decision = rc.decide(
            policy=ExecutionPolicy.EXPERT,
            validation=self._ok_report(),
            plan_confidence=0.99,
            table_count=1,
            estimated_rows=10_000_000,
            explain_max_rows=5_000_000,
        )
        assert decision.action == "confirm"

    def test_expert_not_blocked_by_join_count(self):
        rc = RiskController()
        decision = rc.decide(
            policy=ExecutionPolicy.EXPERT,
            validation=self._ok_report(),
            plan_confidence=0.99,
            table_count=5,
        )
        assert decision.action == "auto_execute"

    def test_expert_auto_executes_when_within_limits(self):
        rc = RiskController()
        decision = rc.decide(
            policy=ExecutionPolicy.EXPERT,
            validation=self._ok_report(),
            plan_confidence=0.99,
            table_count=2,
            estimated_rows=1000,
            explain_max_rows=5_000_000,
        )
        assert decision.action == "auto_execute"


def test_execute_sql_pauses_for_large_limit_then_runs_after_confirmation(tmp_path):
    import sqlite3

    db = tmp_path / "risk.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    conn.commit(); conn.close()

    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    orch = AskOrchestrator(adapter, Session(connection=cfg), NullLLMClient())
    orch._reset_loop_state("show rows", "", True)
    registry = build_tool_registry(orch)
    ctx = ToolContext(execution_policy=ExecutionPolicy.SAFE_AUTO.value)

    first = registry.invoke(
        "execute_sql",
        {
            "sql": "SELECT * FROM t LIMIT 50000",
            "database": "main",
            "limit": 7,
            "timeout_seconds": 12,
            "purpose": "manual export",
            "save_as": "risky_export",
        },
        ctx,
    )
    assert first.ok is True
    assert first.data["pending"] is True
    assert "Confirm before executing" in first.data["question"]
    assert orch.run_state.risk_confirmation["sql_hash"]
    assert orch.run_state.risk_confirmation["execute_args"] == {
        "sql": "SELECT * FROM t LIMIT 50000",
        "database": "main",
        "limit": 7,
        "purpose": "manual export",
        "save_as": "risky_export",
        "timeout_seconds": 12,
    }

    orch.run_state.confirmed_risk_sqls.append(orch.run_state.risk_confirmation["sql_hash"])
    orch.run_state.risk_confirmation = {}
    second = registry.invoke("execute_sql", {"sql": "SELECT * FROM t LIMIT 50000"}, ctx)
    assert second.ok is True
    assert second.data["row_count"] == 1


def test_risk_confirmation_reply_denial_wins_over_execute_word():
    assert _risk_reply_confirms("Execute anyway") is True
    assert _risk_reply_confirms("仍然执行") is True
    assert _risk_reply_confirms("Cancel") is False
    assert _risk_reply_confirms("取消执行") is False
    assert _risk_reply_confirms("不要执行") is False
