"""Metrics for comparing SQL and query results in golden evaluation."""
from __future__ import annotations

import re
from typing import Any


def normalize_sql(sql: str) -> str:
    """Normalize SQL for comparison."""
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\blimit\s+\d+\b", "", s, flags=re.I)
    s = s.strip()
    return s


def compare_sql(actual: str, expected: str | None, pattern: str | None = None) -> dict[str, Any]:
    """Compare actual SQL with expected SQL or pattern.

    Returns:
        {
            "match": bool,
            "match_type": "exact" | "pattern" | "none",
            "actual_normalized": str,
            "expected_normalized": str,
        }
    """
    actual_norm = normalize_sql(actual)

    if expected:
        expected_norm = normalize_sql(expected)
        if actual_norm.lower() == expected_norm.lower():
            return {"match": True, "match_type": "exact", "actual_normalized": actual_norm, "expected_normalized": expected_norm}

    if pattern:
        if re.search(pattern, actual, re.I | re.S):
            return {"match": True, "match_type": "pattern", "actual_normalized": actual_norm, "expected_normalized": pattern}

    return {"match": False, "match_type": "none", "actual_normalized": actual_norm, "expected_normalized": expected or pattern or ""}


def extract_tables_from_sql(sql: str) -> set[str]:
    """Extract table names from SQL (heuristic)."""
    tables = set()
    s = sql.lower()
    for match in re.finditer(r"\bfrom\s+(\w+)", s):
        tables.add(match.group(1))
    for match in re.finditer(r"\bjoin\s+(\w+)", s):
        tables.add(match.group(1))
    return tables


def compare_tables(actual_sql: str, expected_tables: list[str]) -> dict[str, Any]:
    """Check if expected tables are referenced in actual SQL."""
    actual_tables = extract_tables_from_sql(actual_sql)
    expected_set = set(expected_tables)
    missing = expected_set - actual_tables
    extra = actual_tables - expected_set - {"dual"}
    return {
        "match": len(missing) == 0,
        "actual_tables": sorted(actual_tables),
        "expected_tables": sorted(expected_set),
        "missing": sorted(missing),
        "extra": sorted(extra),
    }


def compare_columns(actual_columns: list[str], expected_columns: list[str]) -> dict[str, Any]:
    """Compare actual and expected column names."""
    actual_set = set(c.lower() for c in actual_columns)
    expected_set = set(c.lower() for c in expected_columns)
    missing = expected_set - actual_set
    return {
        "match": len(missing) == 0,
        "actual_columns": sorted(actual_columns),
        "expected_columns": sorted(expected_columns),
        "missing": sorted(missing),
    }


def compare_result(
    *,
    actual_sql: str = "",
    actual_columns: list[str] | None = None,
    actual_row_count: int = 0,
    expected_sql: str = "",
    expected_sql_pattern: str = "",
    expected_tables: list[str] | None = None,
    expected_columns: list[str] | None = None,
    expected_row_count_min: int | None = None,
) -> dict[str, Any]:
    """Compare actual result with expected golden case."""
    checks = []
    all_pass = True

    # SQL check
    if expected_sql or expected_sql_pattern:
        sql_result = compare_sql(actual_sql, expected_sql or None, expected_sql_pattern or None)
        checks.append({"check": "sql", "pass": sql_result["match"], "details": sql_result})
        if not sql_result["match"]:
            all_pass = False

    # Table check
    if expected_tables:
        table_result = compare_tables(actual_sql, expected_tables)
        checks.append({"check": "tables", "pass": table_result["match"], "details": table_result})
        if not table_result["match"]:
            all_pass = False

    # Column check
    if expected_columns and actual_columns:
        col_result = compare_columns(actual_columns, expected_columns)
        checks.append({"check": "columns", "pass": col_result["match"], "details": col_result})
        if not col_result["match"]:
            all_pass = False

    # Row count check
    if expected_row_count_min is not None:
        row_pass = actual_row_count >= expected_row_count_min
        checks.append({
            "check": "row_count",
            "pass": row_pass,
            "details": {"actual": actual_row_count, "expected_min": expected_row_count_min},
        })
        if not row_pass:
            all_pass = False

    return {
        "pass": all_pass,
        "checks": checks,
    }
