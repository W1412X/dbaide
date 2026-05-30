"""SQL validation guard for DBAide - enhanced for production safety."""
from __future__ import annotations

import re

from dbaide.core.errors import DBAideError, ErrorCode, RepairAction
from dbaide.core.result import ValidationReport
from dbaide.models import ValidationIssue, ValidationResult


# ─────────────────────────────────────────────────────────────────────────────
# Forbidden keywords and patterns
# ─────────────────────────────────────────────────────────────────────────────

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "replace", "merge", "grant", "revoke",
    "attach", "detach", "vacuum",
    "prepare", "execute", "deallocate", "call",
}

FORBIDDEN_PATTERNS = [
    re.compile(r"\bload_file\s*\(", re.I),
    re.compile(r"\bsleep\s*\(", re.I),
    re.compile(r"\bbenchmark\s*\(", re.I),
    re.compile(r"\bpg_sleep\s*\(", re.I),
    re.compile(r"\bcopy\s+.*\bprogram\b", re.I | re.S),
    re.compile(r"\binto\s+(outfile|dumpfile)\b", re.I),
    re.compile(r"\bload\s+(data|xml)\s+\w*\s*infile\b", re.I),
    re.compile(r"/\*!", re.I),  # MySQL executable conditional comment
    re.compile(r"\bexecute\s+immediate\b", re.I),  # PostgreSQL dynamic SQL
]

# Patterns that indicate high-risk queries
HIGH_RISK_PATTERNS = [
    re.compile(r"\bselect\s+\*\s+from\s+\w+\s*$", re.I | re.S),  # SELECT * without WHERE
    re.compile(r"\bjoin\s+\w+\s+on\s+1\s*=\s*1\b", re.I),  # Cartesian join
    re.compile(r"\bwhere\s+1\s*=\s*1\b", re.I),  # Always-true filter
    re.compile(r"\border\s+by\s+rand\s*\(\s*\)", re.I),  # ORDER BY RAND()
    re.compile(r"\border\s+by\s+random\s*\(\s*\)", re.I),  # ORDER BY RANDOM()
]

# Sensitive column patterns
SENSITIVE_COLUMN_PATTERNS = [
    re.compile(r"\b(email|e_mail)\b", re.I),
    re.compile(r"\b(phone|mobile|tel)\b", re.I),
    re.compile(r"\b(password|passwd|pwd)\b", re.I),
    re.compile(r"\b(token|secret|api_key|apikey)\b", re.I),
    re.compile(r"\b(ssn|id_card|identity)\b", re.I),
    re.compile(r"\b(address|addr)\b", re.I),
]


# ─────────────────────────────────────────────────────────────────────────────
# SQLGuard
# ─────────────────────────────────────────────────────────────────────────────

class SQLGuard:
    """SQL validation guard with safety checks and risk assessment."""

    def __init__(self, *, default_limit: int = 100) -> None:
        self.default_limit = default_limit

    def validate(self, sql: str, *, add_limit: bool = True) -> ValidationResult:
        """Validate SQL for safety and correctness."""
        issues: list[ValidationIssue] = []
        normalized = sql.strip()

        if not normalized:
            return ValidationResult(ok=False, issues=[ValidationIssue("EMPTY_SQL", "SQL is empty")])

        # Check: multiple statements
        if self._has_multiple_statements(normalized):
            issues.append(ValidationIssue("MULTI_STATEMENT", "Only one SQL statement is allowed"))

        # Check: first keyword must be SELECT/WITH/EXPLAIN
        first = self._first_keyword(normalized)
        if first not in {"select", "with", "explain"}:
            issues.append(ValidationIssue("READONLY_ONLY", "Only SELECT/WITH/EXPLAIN statements are allowed"))

        # Check: forbidden keywords
        stripped = _strip_strings_and_comments(normalized).lower()
        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", stripped):
                issues.append(ValidationIssue("FORBIDDEN_KEYWORD", f"Forbidden keyword: {keyword.upper()}"))

        # Check: forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(normalized):
                issues.append(ValidationIssue("FORBIDDEN_FUNCTION", f"Forbidden SQL pattern: {pattern.pattern}"))

        # Add limit if needed
        if not issues and add_limit and first in {"select", "with"}:
            normalized = self.ensure_limit(normalized, self.default_limit)

        return ValidationResult(ok=not issues, issues=issues, normalized_sql=normalized.rstrip(";"))

    def validate_with_report(
        self,
        sql: str,
        *,
        add_limit: bool = True,
        known_tables: set[str] | None = None,
        known_columns: set[str] | None = None,
    ) -> ValidationReport:
        """Extended validation returning a full ValidationReport with risk assessment."""
        result = self.validate(sql, add_limit=add_limit)

        warnings = []
        risk_level = "low"
        requires_confirmation = False

        if not result.ok:
            return ValidationReport(
                ok=False,
                normalized_sql=result.normalized_sql,
                issues=[issue.message for issue in result.issues],
                warnings=warnings,
                risk_level="rejected",
                requires_confirmation=False,
            )

        normalized = result.normalized_sql
        stripped = _strip_strings_and_comments(normalized).lower()

        # Check: schema validation
        if known_tables:
            tables_in_sql = self._extract_tables(stripped)
            unknown_tables = tables_in_sql - known_tables - {"dual"}
            if unknown_tables:
                warnings.append(f"Unknown tables: {', '.join(unknown_tables)}")
                risk_level = "medium"

        if known_columns:
            columns_in_sql = self._extract_columns(stripped)
            # This is a heuristic - we can't reliably extract all column references
            # But we can flag obviously wrong ones

        # Check: high-risk patterns
        for pattern in HIGH_RISK_PATTERNS:
            if pattern.search(normalized):
                warnings.append(f"High-risk pattern detected: {pattern.pattern}")
                risk_level = "high"
                requires_confirmation = True

        # Check: sensitive columns
        for pattern in SENSITIVE_COLUMN_PATTERNS:
            if pattern.search(stripped):
                warnings.append("Query may access sensitive columns")
                if risk_level == "low":
                    risk_level = "medium"
                break

        # Check: no WHERE clause on SELECT *
        if re.search(r"\bselect\s+\*\s+from\b", stripped) and "where" not in stripped:
            warnings.append("SELECT * without WHERE clause may return large result set")
            if risk_level == "low":
                risk_level = "medium"

        # Check: large limit
        limit_match = re.search(r"\blimit\s+(\d+)\b", stripped)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > 10000:
                warnings.append(f"Large LIMIT ({limit_val}) may cause performance issues")
                risk_level = "high"
                requires_confirmation = True

        return ValidationReport(
            ok=True,
            normalized_sql=normalized,
            issues=[],
            warnings=warnings,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
        )

    def ensure_limit(self, sql: str, limit: int) -> str:
        """Ensure SQL has a LIMIT clause."""
        stripped = sql.strip().rstrip(";")
        if re.search(r"\blimit\s+\d+\b", stripped, re.I):
            return stripped
        return f"{stripped} LIMIT {int(limit)}"

    def _first_keyword(self, sql: str) -> str:
        match = re.search(r"[A-Za-z]+", _strip_leading_comments(sql))
        return match.group(0).lower() if match else ""

    def _has_multiple_statements(self, sql: str) -> bool:
        stripped = _strip_strings_and_comments(sql)
        return ";" in stripped.strip().rstrip(";")

    def _extract_tables(self, sql: str) -> set[str]:
        """Extract table names from SQL (heuristic)."""
        tables = set()
        # FROM clause
        for match in re.finditer(r"\bfrom\s+(\w+)", sql):
            tables.add(match.group(1).lower())
        # JOIN clause
        for match in re.finditer(r"\bjoin\s+(\w+)", sql):
            tables.add(match.group(1).lower())
        # UPDATE clause
        for match in re.finditer(r"\bupdate\s+(\w+)", sql):
            tables.add(match.group(1).lower())
        # INSERT INTO
        for match in re.finditer(r"\binto\s+(\w+)", sql):
            tables.add(match.group(1).lower())
        return tables

    def _extract_columns(self, sql: str) -> set[str]:
        """Extract column names from SQL (heuristic)."""
        columns = set()
        # This is a best-effort extraction
        # We can't reliably parse SQL without a proper parser
        for match in re.finditer(r"\b(\w+)\s*=", sql):
            columns.add(match.group(1).lower())
        return columns


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _strip_leading_comments(sql: str) -> str:
    text = sql.lstrip()
    while True:
        if text.startswith("--"):
            pos = text.find("\n")
            text = "" if pos < 0 else text[pos + 1:].lstrip()
            continue
        if text.startswith("/*"):
            pos = text.find("*/")
            text = "" if pos < 0 else text[pos + 2:].lstrip()
            continue
        return text


def _strip_strings_and_comments(sql: str) -> str:
    out: list[str] = []
    i = 0
    quote = ""
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                if nxt == quote:
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            continue
        if ch == "-" and nxt == "-":
            pos = sql.find("\n", i + 2)
            i = len(sql) if pos < 0 else pos + 1
            continue
        if ch == "/" and nxt == "*":
            if i + 2 < len(sql) and sql[i + 2] == "!":
                out.append(ch)
                i += 1
                continue
            pos = sql.find("*/", i + 2)
            i = len(sql) if pos < 0 else pos + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)
