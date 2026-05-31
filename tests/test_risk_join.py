"""Tests for join confidence risk signal and schema context helpers."""

from __future__ import annotations

from dbaide.agent.controllers import RiskController
from dbaide.agent.schema_context import join_confidence_for_sql
from dbaide.core.result import ExecutionPolicy, ValidationReport


def test_join_confidence_for_sql_uses_matching_edges():
    relations = [
        {"table": "orders", "ref_table": "users", "confidence": 0.95},
        {"table": "orders", "ref_table": "products", "confidence": 0.55},
    ]
    sql = "SELECT * FROM orders JOIN users ON orders.user_id = users.id"
    assert join_confidence_for_sql(relations, sql) == 0.95


def test_join_confidence_for_sql_falls_back_to_all_relations():
    relations = [
        {"table": "orders", "ref_table": "users", "confidence": 0.72},
        {"table": "orders", "ref_table": "products", "confidence": 0.61},
    ]
    sql = "SELECT * FROM orders"
    assert join_confidence_for_sql(relations, sql) == 0.61


def test_join_confidence_for_sql_empty_relations():
    assert join_confidence_for_sql([], "SELECT * FROM a JOIN b ON a.id = b.id") == 1.0


def test_risk_controller_low_join_confidence_requests_confirm():
    risk = RiskController()
    decision = risk.decide(
        policy=ExecutionPolicy.SAFE_AUTO,
        validation=ValidationReport(ok=True, normalized_sql="SELECT 1", issues=[]),
        plan_confidence=0.9,
        has_joins=True,
        join_confidence=0.62,
    )
    assert decision.action == "confirm"
    assert "join confidence" in decision.reason.lower()


def test_risk_controller_high_join_confidence_auto_executes():
    risk = RiskController()
    decision = risk.decide(
        policy=ExecutionPolicy.SAFE_AUTO,
        validation=ValidationReport(ok=True, normalized_sql="SELECT 1", issues=[]),
        plan_confidence=0.9,
        table_count=2,
        has_joins=True,
        join_confidence=0.99,
    )
    assert decision.action == "auto_execute"
