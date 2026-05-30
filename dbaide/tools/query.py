from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.context.disclosure import DisclosureContext
from dbaide.core.result import ValidationReport
from dbaide.models import QueryResult, ValidationResult
from dbaide.validation import SchemaGuard, SQLGuard


class QueryTools:
    def __init__(self, adapter: DatabaseAdapter, context: DisclosureContext, *, instance: str = "", default_limit: int = 100, timeout_seconds: int = 10) -> None:
        self.adapter = adapter
        self.context = context
        self.instance = instance or adapter.config.name
        self.sql_guard = SQLGuard(default_limit=default_limit)
        self.schema_guard = SchemaGuard()
        self.timeout_seconds = timeout_seconds

    def validate_sql(self, sql: str, *, add_limit: bool = True) -> ValidationResult:
        first = self.sql_guard.validate(sql, add_limit=add_limit)
        if not first.ok:
            return first
        second = self.schema_guard.validate(first.normalized_sql, self.context)
        if not second.ok:
            return second
        return first

    def validate_sql_report(self, sql: str, *, add_limit: bool = True) -> ValidationReport:
        report = self.sql_guard.validate_with_report(sql, add_limit=add_limit)
        if not report.ok:
            return report
        schema_result = self.schema_guard.validate(report.normalized_sql, self.context)
        if not schema_result.ok:
            return ValidationReport(
                ok=False,
                normalized_sql=report.normalized_sql,
                issues=[issue.message for issue in schema_result.issues],
                warnings=report.warnings,
                risk_level="rejected",
                requires_confirmation=False,
            )
        return report

    def explain_sql(self, sql: str, *, database: str = "") -> QueryResult:
        validation = self.sql_guard.validate(sql, add_limit=False)
        if not validation.ok:
            raise ValueError("; ".join(issue.message for issue in validation.issues))
        explain_target = _strip_leading_explain(validation.normalized_sql)
        result = self.adapter.explain(explain_target, database=database, timeout_seconds=self.timeout_seconds)
        self.context.record_execution(result.sql, instance=self.instance, database=database)
        return result

    def execute_sql(
        self,
        sql: str,
        *,
        database: str = "",
        limit: int = 100,
        preflight_explain: bool = False,
    ) -> QueryResult:
        validation = self.validate_sql(sql, add_limit=True)
        if not validation.ok:
            raise ValueError("; ".join(issue.message for issue in validation.issues))
        normalized = validation.normalized_sql
        if preflight_explain:
            explain_target = _strip_leading_explain(normalized)
            try:
                explain_result = self.adapter.explain(
                    explain_target, database=database, timeout_seconds=self.timeout_seconds,
                )
                self.context.record_execution(explain_result.sql, instance=self.instance, database=database)
            except (ValueError, RuntimeError, OSError):
                pass
        result = self.adapter.execute_readonly(
            normalized, database=database, limit=limit, timeout_seconds=self.timeout_seconds,
        )
        self.context.record_execution(result.sql, instance=self.instance, database=database)
        return result


def _strip_leading_explain(sql: str) -> str:
    text = sql.strip().rstrip(";")
    lowered = text.lower()
    if lowered.startswith("explain "):
        return text[8:].strip()
    return text
