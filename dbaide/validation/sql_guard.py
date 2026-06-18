"""SQL validation guard for DBAide - enhanced for production safety."""
from __future__ import annotations

import re

from dbaide.core.result import ValidationReport
from dbaide.models import ValidationIssue, ValidationResult
from dbaide.validation.sql_cleanup import strip_function_from_keywords


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
    # Read-side file / command / network functions. These need no write to do
    # damage (read server files, exfiltrate, SSRF, run OS commands), so they're
    # blocked even though the connection is already read-only. Matched only in the
    # FUNC( call form so a same-named plain column is unaffected.
    re.compile(r"\bpg_read_(?:binary_)?file\s*\(", re.I),     # postgres: read server files
    re.compile(r"\bpg_ls_dir\s*\(", re.I),                     # postgres: list server dirs
    re.compile(r"\bpg_stat_file\s*\(", re.I),                  # postgres: stat server files
    re.compile(r"\blo_(?:import|export)\s*\(", re.I),          # postgres: large-object file I/O
    re.compile(r"\bdblink(?:_connect)?\s*\(", re.I),           # postgres: outbound DB conn (SSRF)
    re.compile(r"\bxp_cmdshell\b", re.I),                      # mssql: OS command exec
    re.compile(r"\bxp_(?:dirtree|fileexist|regread|regwrite)\b", re.I),  # mssql: fs/registry
    re.compile(r"\bsp_oa(?:create|method|getproperty)\b", re.I),  # mssql: OLE automation
    re.compile(r"\bopen(?:rowset|datasource)\s*\(", re.I),     # mssql: ad-hoc remote/file access
    re.compile(r"\bsys_e(?:xec|val)\s*\(", re.I),              # mysql UDF: OS command exec
    re.compile(r"\bload_extension\s*\(", re.I),                # sqlite: load native extension
    re.compile(r"\b(?:readfile|writefile|fts3_tokenizer)\s*\(", re.I),  # sqlite: file I/O
    re.compile(r"\butl_(?:file|http|smtp|tcp|inaddr)\b", re.I),  # oracle: file/network access
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

    def __init__(self, *, default_limit: int = 100, max_row_limit: int = 1000,
                 dialect: str = "generic") -> None:
        self.default_limit = default_limit
        self.dialect = str(dialect or "generic").lower()
        # Hard ceiling: a LIMIT above this is rejected outright (not merely warned).
        self.max_row_limit = max(1, int(max_row_limit))
        # SELECT * without WHERE is forced to at most this many rows.
        self.unfiltered_star_limit = min(100, self.default_limit)

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

        # Check: forbidden keywords. A keyword counts as forbidden only in
        # *statement* position — not as a function call (``REPLACE(...)``,
        # ``TRUNCATE(n,d)``, MySQL ``INSERT(...)``) nor as a qualified identifier
        # (``t.call``). The negative lookbehind ``(?<!\.)`` drops qualified names;
        # the negative lookahead ``(?!\s*\()`` drops function calls. Real DML —
        # ``DELETE FROM``, ``REPLACE INTO``, a data-modifying CTE ``AS (DELETE …)``
        # — is followed by whitespace + a target, so it is still flagged.
        stripped = _strip_strings_and_comments(normalized, dialect=self.dialect).lower()
        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"(?<!\.)\b{keyword}\b(?!\s*\()", stripped):
                issues.append(ValidationIssue("FORBIDDEN_KEYWORD", f"Forbidden keyword: {keyword.upper()}"))

        # Check: forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(normalized):
                issues.append(ValidationIssue("FORBIDDEN_FUNCTION", f"Forbidden SQL pattern: {pattern.pattern}"))

        issues.extend(self._dialect_issues(stripped))

        explicit_limit = _explicit_limit(stripped, dialect=self.dialect)

        if not issues and first in {"select", "with"}:
            # SELECT * without WHERE is forced to a small bound regardless of add_limit.
            if _is_unfiltered_star(stripped) and explicit_limit is None:
                normalized = self.ensure_limit(normalized, self.unfiltered_star_limit)
            elif add_limit:
                normalized = self.ensure_limit(normalized, self.default_limit)

        return ValidationResult(ok=not issues, issues=issues, normalized_sql=normalized.rstrip(";"))

    def _dialect_issues(self, stripped_sql: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if self.dialect in {"mysql", "mariadb"} and re.search(r"\bfull\s+(?:outer\s+)?join\b", stripped_sql):
            issues.append(ValidationIssue(
                "UNSUPPORTED_DIALECT_SYNTAX",
                "MySQL/MariaDB does not support FULL OUTER JOIN. Rewrite it as a UNION of "
                "LEFT JOIN and RIGHT JOIN, with an anti-duplicate filter such as WHERE left_key IS NULL.",
            ))
        return issues

    def validate_with_report(
        self,
        sql: str,
        *,
        add_limit: bool = True,
        known_tables: set[str] | None = None,
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
        stripped = _strip_strings_and_comments(normalized, dialect=self.dialect).lower()

        # Check: schema validation
        if known_tables:
            tables_in_sql = self._extract_tables(stripped)
            unknown_tables = tables_in_sql - known_tables - {"dual"}
            if unknown_tables:
                warnings.append(f"Unknown tables: {', '.join(unknown_tables)}")
                risk_level = "medium"

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

        # Check: large limit. This used to be a hard cap; now it asks for user
        # confirmation so a deliberate large export/query is still possible.
        limit_val = _explicit_limit(stripped, dialect=self.dialect)
        if limit_val is not None and limit_val > self.max_row_limit:
            warnings.append(
                f"LIMIT {limit_val} exceeds the configured confirmation threshold ({self.max_row_limit})"
            )
            risk_level = "high"
            requires_confirmation = True
        elif limit_val is not None and limit_val > max(1000, self.max_row_limit // 2):
            warnings.append(f"Large LIMIT ({limit_val}) may cause performance issues")
            if risk_level == "low":
                risk_level = "medium"

        # Check: UNION fan-out
        union_count = len(re.findall(r"\bunion\b", stripped))
        if union_count >= 3:
            warnings.append(f"Query contains {union_count} UNIONs")
            if risk_level != "high":
                risk_level = "medium"

        # Check: deep nested subqueries
        if _max_paren_depth(stripped) >= 4:
            warnings.append("Query has deeply nested subqueries")
            if risk_level == "low":
                risk_level = "medium"

        return ValidationReport(
            ok=True,
            normalized_sql=normalized,
            issues=[],
            warnings=warnings,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
        )

    def ensure_limit(self, sql: str, limit: int) -> str:
        """Ensure the SQL has a *top-level* LIMIT clause (subquery/CTE limits don't count)."""
        # Delegate to the canonical, comment-safe appender (appends LIMIT on a new
        # line so a trailing line comment can't swallow it; no-op if a top-level
        # limiter already exists).
        from dbaide.adapters.base import append_limit
        return append_limit(sql, int(limit), dialect=self.dialect)

    def _first_keyword(self, sql: str) -> str:
        match = re.search(r"[A-Za-z]+", _strip_leading_comments(sql))
        return match.group(0).lower() if match else ""

    def _has_multiple_statements(self, sql: str) -> bool:
        stripped = _strip_strings_and_comments(sql, dialect=self.dialect)
        # Drop trailing semicolons AND whitespace so harmless trailing empty
        # statements ("SELECT 1 ; ;") aren't misread as multiple statements.
        core = re.sub(r"[\s;]+$", "", stripped)
        return ";" in core

    def _extract_tables(self, sql: str) -> set[str]:
        """Extract table names from SQL (heuristic)."""
        tables = set()
        cleaned = strip_function_from_keywords(sql)
        ident = r"(?:\w+|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])"
        qualified = rf"({ident}(?:\s*\.\s*{ident})*)"
        for keyword in ("from", "join", "update", "into"):
            for match in re.finditer(rf"\b{keyword}\s+{qualified}", cleaned):
                raw = match.group(1)
                parts = [p.strip().strip('`"[]') for p in re.split(r"\s*\.\s*", raw)]
                tables.add(".".join(p.lower() for p in parts if p))
        return tables


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _explicit_limit(sql: str, *, dialect: str = "generic") -> int | None:
    """Top-level LIMIT row-count if present. Delegates to the dialect-aware
    parser so subquery/CTE/string LIMITs and ``LIMIT offset, count`` are handled."""
    from dbaide.adapters.base import outer_limit_value
    return outer_limit_value(sql, dialect=dialect)


def _is_unfiltered_star(stripped_lower_sql: str) -> bool:
    return bool(re.search(r"\bselect\s+\*\s+from\b", stripped_lower_sql)) and "where" not in stripped_lower_sql


def _max_paren_depth(sql: str) -> int:
    depth = 0
    best = 0
    for ch in sql:
        if ch == "(":
            depth += 1
            best = max(best, depth)
        elif ch == ")":
            depth = max(0, depth - 1)
    return best


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


def _strip_strings_and_comments(sql: str, *, dialect: str = "generic") -> str:
    out: list[str] = []
    i = 0
    quote = ""
    # Only MySQL/MariaDB treats backslash as a string escape character.
    # Standard SQL (PostgreSQL, SQLite, etc.) uses '' for escaping and
    # backslash is a literal character.  Treating it as an escape in
    # non-MySQL dialects makes the parser lose track of quote boundaries.
    backslash_escapes = dialect in ("mysql", "mariadb")
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            if backslash_escapes and ch == "\\":
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
