"""Tests for the agent SQL hard gates: max LIMIT, unfiltered SELECT *, EXPLAIN cost, joins."""

from __future__ import annotations

from dbaide.core.result import ExecutionPolicy, ValidationReport
from dbaide.agent.controllers import RiskController
from dbaide.validation.sql_guard import SQLGuard


class TestSQLGuardHardCaps:
    def test_limit_above_max_is_rejected(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        result = guard.validate("SELECT * FROM t LIMIT 50000")
        assert result.ok is False
        assert any(i.code == "LIMIT_TOO_LARGE" for i in result.issues)

    def test_limit_within_max_ok(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        assert guard.validate("SELECT id FROM t LIMIT 500").ok is True

    def test_unfiltered_select_star_is_capped(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        result = guard.validate("SELECT * FROM users")
        assert result.ok is True
        assert "limit 100" in result.normalized_sql.lower()

    def test_many_joins_flagged_high(self):
        guard = SQLGuard(default_limit=100, max_row_limit=1000)
        sql = "SELECT * FROM a JOIN b ON a.id=b.a JOIN c ON c.b=b.id JOIN d ON d.c=c.id WHERE a.x=1"
        report = guard.validate_with_report(sql)
        assert report.risk_level == "high"
        assert report.requires_confirmation is True


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

    def test_expert_blocked_by_join_count(self):
        rc = RiskController()
        decision = rc.decide(
            policy=ExecutionPolicy.EXPERT,
            validation=self._ok_report(),
            plan_confidence=0.99,
            table_count=5,
            max_join_tables=3,
        )
        assert decision.action == "confirm"

    def test_expert_auto_executes_when_within_limits(self):
        rc = RiskController()
        decision = rc.decide(
            policy=ExecutionPolicy.EXPERT,
            validation=self._ok_report(),
            plan_confidence=0.99,
            table_count=2,
            estimated_rows=1000,
            explain_max_rows=5_000_000,
            max_join_tables=3,
        )
        assert decision.action == "auto_execute"
