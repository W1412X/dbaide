from __future__ import annotations

import re

from dbaide.adapters.base import DatabaseAdapter
from dbaide.context.disclosure import DisclosureContext
from dbaide.core.result import ValidationReport
from dbaide.models import QueryResult, ValidationResult
from dbaide.validation import SQLGuard, TableScopeGuard


class QueryTools:
    def __init__(self, adapter: DatabaseAdapter, context: DisclosureContext, *, instance: str = "",
                 default_limit: int | None = None, timeout_seconds: int | None = None,
                 max_row_limit: int | None = None) -> None:
        self.adapter = adapter
        self.context = context
        self.instance = instance or adapter.config.name
        # Defaults come from the adapter's resource policy unless explicitly overridden.
        policy = getattr(adapter, "policy", None)
        if default_limit is None:
            default_limit = policy.default_row_limit if policy else 100
        if timeout_seconds is None:
            timeout_seconds = policy.statement_timeout_seconds if policy else 60
        if max_row_limit is None:
            max_row_limit = policy.max_row_limit if policy else 1000
        self.sql_guard = SQLGuard(
            default_limit=default_limit,
            max_row_limit=max_row_limit,
            dialect=getattr(adapter, "dialect", "generic"),
        )
        # OPT-IN per-connection table scope (default: allow all → no-op). Existence
        # of a table/column is NOT pre-checked here — the DB returns a precise error
        # the agent can act on, which is cheaper to maintain than a static gate.
        self.scope_guard = TableScopeGuard(
            allow=list(getattr(adapter.config, "table_allow", []) or []),
            deny=list(getattr(adapter.config, "table_deny", []) or []),
        )
        self.timeout_seconds = timeout_seconds
        self.explain_max_rows = policy.explain_max_rows if policy else 0

    def _guard_for_limit(self, limit: int | None) -> SQLGuard:
        if limit is None:
            return self.sql_guard
        return SQLGuard(
            default_limit=max(1, int(limit)),
            max_row_limit=self.sql_guard.max_row_limit,
            dialect=self.sql_guard.dialect,
        )

    def estimate_rows(self, sql: str, *, database: str = "") -> int | None:
        """Best-effort EXPLAIN row estimate for cost gating (None if unavailable)."""
        try:
            return self.adapter.explain_estimated_rows(sql, database=database)
        except Exception:
            return None

    def validate_sql(self, sql: str, *, add_limit: bool = True, limit: int | None = None) -> ValidationResult:
        first = self._guard_for_limit(limit).validate(sql, add_limit=add_limit)
        if not first.ok:
            return first
        scope = self.scope_guard.validate(first.normalized_sql)
        if not scope.ok:
            return scope
        return first

    def validate_sql_report(self, sql: str, *, add_limit: bool = True, limit: int | None = None) -> ValidationReport:
        report = self._guard_for_limit(limit).validate_with_report(sql, add_limit=add_limit)
        if not report.ok:
            return report
        scope = self.scope_guard.validate(report.normalized_sql)
        if not scope.ok:
            return ValidationReport(
                ok=False,
                normalized_sql=report.normalized_sql,
                issues=[issue.message for issue in scope.issues],
                warnings=report.warnings,
                risk_level="rejected",
                requires_confirmation=False,
            )
        return report

    def explain_sql(self, sql: str, *, database: str = "") -> QueryResult:
        validation = self.sql_guard.validate(sql, add_limit=False)
        if not validation.ok:
            raise ValueError("; ".join(issue.message for issue in validation.issues))
        # Honor the optional connection table-scope on EXPLAIN too, so it can't be
        # used to probe a denied/out-of-scope table.
        scope = self.scope_guard.validate(validation.normalized_sql)
        if not scope.ok:
            raise ValueError("; ".join(issue.message for issue in scope.issues))
        explain_target = _strip_leading_explain(validation.normalized_sql)
        result = self.adapter.explain(explain_target, database=database, timeout_seconds=self.timeout_seconds)
        self.context.record_execution(result.sql, database=database)
        return result

    def execute_sql(
        self,
        sql: str,
        *,
        database: str = "",
        limit: int | None = None,
        timeout_seconds: int | None = None,
        preflight_explain: bool = False,
        confirmed: bool = False,
    ) -> QueryResult:
        effective_limit = self.sql_guard.default_limit if limit is None else max(1, int(limit))
        report = self.validate_sql_report(sql, add_limit=True, limit=effective_limit)
        if not report.ok:
            raise ValueError("; ".join(report.issues))
        if report.requires_confirmation and not confirmed:
            raise PermissionError("; ".join(report.warnings) or "SQL requires confirmation")
        normalized = report.normalized_sql
        if preflight_explain:
            explain_target = _strip_leading_explain(normalized)
            try:
                explain_result = self.adapter.explain(
                    explain_target, database=database, timeout_seconds=timeout_seconds or self.timeout_seconds,
                )
                self.context.record_execution(explain_result.sql, database=database)
            except (ValueError, RuntimeError, OSError):
                pass
        result = self.adapter.execute_readonly(
            normalized, database=database, limit=effective_limit, timeout_seconds=timeout_seconds or self.timeout_seconds,
        )
        self.context.record_execution(result.sql, database=database)
        return result


def _strip_leading_explain(sql: str) -> str:
    text = sql.strip().rstrip(";")
    m = re.match(r"explain\s+(?:analyze\s+)?(?:query\s+plan\s+)?", text, re.IGNORECASE)
    if m:
        return text[m.end():].strip()
    return text
