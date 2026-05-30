"""Golden evaluation framework for DBAide."""
from dbaide.eval.golden import GoldenCase, GoldenSuite, load_golden_suite
from dbaide.eval.runner import GoldenRunner
from dbaide.eval.metrics import compare_sql, compare_result

__all__ = [
    "GoldenCase",
    "GoldenSuite",
    "GoldenRunner",
    "load_golden_suite",
    "compare_sql",
    "compare_result",
]
