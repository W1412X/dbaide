"""Golden test runner for DBAide evaluation."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from dbaide.core.result import WorkflowRequest, WorkflowResult
from dbaide.core.workflow import WorkflowEngine
from dbaide.eval.golden import GoldenCase, GoldenSuite
from dbaide.eval.metrics import compare_result
from dbaide.models import ConnectionConfig

logger = logging.getLogger("dbaide.eval")


class GoldenResult:
    """Result of evaluating a single golden case."""

    __slots__ = ("case_id", "question", "pass", "checks", "actual_sql", "actual_columns", "actual_row_count", "elapsed_ms", "error")

    def __init__(self, **kwargs) -> None:
        self.case_id = kwargs.get("case_id", "")
        self.question = kwargs.get("question", "")
        self.pass_ = kwargs.get("pass", False)
        self.checks = kwargs.get("checks", [])
        self.actual_sql = kwargs.get("actual_sql", "")
        self.actual_columns = kwargs.get("actual_columns", [])
        self.actual_row_count = kwargs.get("actual_row_count", 0)
        self.elapsed_ms = kwargs.get("elapsed_ms", 0.0)
        self.error = kwargs.get("error", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "pass": self.pass_,
            "checks": self.checks,
            "actual_sql": self.actual_sql,
            "actual_columns": self.actual_columns,
            "actual_row_count": self.actual_row_count,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


class GoldenSuiteResult:
    """Result of evaluating a full golden suite."""

    __slots__ = ("suite_name", "total", "passed", "failed", "results", "elapsed_ms")

    def __init__(self, **kwargs) -> None:
        self.suite_name = kwargs.get("suite_name", "")
        self.total = kwargs.get("total", 0)
        self.passed = kwargs.get("passed", 0)
        self.failed = kwargs.get("failed", 0)
        self.results = kwargs.get("results", [])
        self.elapsed_ms = kwargs.get("elapsed_ms", 0.0)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": f"{self.pass_rate:.1%}",
            "elapsed_ms": self.elapsed_ms,
            "results": [r.to_dict() for r in self.results],
        }

    def summary(self) -> str:
        lines = [
            f"Suite: {self.suite_name}",
            f"Total: {self.total}, Passed: {self.passed}, Failed: {self.failed}",
            f"Pass rate: {self.pass_rate:.1%}",
            f"Elapsed: {self.elapsed_ms:.0f}ms",
        ]
        if self.failed > 0:
            lines.append("")
            lines.append("Failed cases:")
            for r in self.results:
                if not r.pass_:
                    lines.append(f"  - {r.case_id}: {r.question}")
                    if r.error:
                        lines.append(f"    Error: {r.error}")
        return "\n".join(lines)


class GoldenRunner:
    """Runs golden test suites against a WorkflowEngine."""

    def __init__(self, engine: WorkflowEngine) -> None:
        self.engine = engine

    def run_suite(
        self,
        suite: GoldenSuite,
        *,
        tags: list[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> GoldenSuiteResult:
        """Run all cases in a suite."""
        cases = suite.cases
        if tags:
            cases = [c for c in cases if any(t in c.tags for t in tags)]

        results = []
        start = time.perf_counter()

        for i, case in enumerate(cases):
            if progress:
                progress(f"[{i+1}/{len(cases)}] {case.id}: {case.question}")
            result = self.run_case(case)
            results.append(result)

        elapsed = (time.perf_counter() - start) * 1000
        passed = sum(1 for r in results if r.pass_)

        return GoldenSuiteResult(
            suite_name=suite.name,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            results=results,
            elapsed_ms=elapsed,
        )

    def run_case(self, case: GoldenCase) -> GoldenResult:
        """Run a single golden case."""
        start = time.perf_counter()
        try:
            request = WorkflowRequest(
                question=case.question,
                connection_name=self.engine.connection.name,
            )
            wf_result = self.engine.run(request)
            elapsed = (time.perf_counter() - start) * 1000

            actual_sql = wf_result.selected_sql
            actual_columns = []
            actual_row_count = 0
            if wf_result.execution_result:
                actual_columns = getattr(wf_result.execution_result, "columns", [])
                actual_row_count = getattr(wf_result.execution_result, "row_count", 0)

            comparison = compare_result(
                actual_sql=actual_sql,
                actual_columns=actual_columns,
                actual_row_count=actual_row_count,
                expected_sql=case.expected_sql,
                expected_sql_pattern=case.expected_sql_pattern,
                expected_tables=case.expected_tables,
                expected_columns=case.expected_columns,
                expected_row_count_min=case.expected_row_count_min,
            )

            return GoldenResult(
                case_id=case.id,
                question=case.question,
                pass_=comparison["pass"],
                checks=comparison["checks"],
                actual_sql=actual_sql,
                actual_columns=actual_columns,
                actual_row_count=actual_row_count,
                elapsed_ms=elapsed,
            )

        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return GoldenResult(
                case_id=case.id,
                question=case.question,
                pass_=False,
                checks=[],
                elapsed_ms=elapsed,
                error=str(exc),
            )
