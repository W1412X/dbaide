"""Optional, stateless table-scope guard.

This replaces the old progressive-disclosure gate (which rejected any table not
"disclosed" earlier in the session). That gate was stateful, high-maintenance,
noise-prone (false-rejecting valid SQL), and provided weak security — the engine
runs read-only and the agent discovers tables anyway, so existence is best proven
by simply executing and reading the DB's error.

What remains here is an OPT-IN security scope: when a connection configures an
``table_allow`` / ``table_deny`` list, SQL that references a table outside the
allow-list (or inside the deny-list) is rejected. With no scope configured
(the default) this is a no-op, so it adds zero noise to normal use.

The table-reference extraction deliberately strips comments/strings first so a
denied table cannot be hidden from the scope check by a comment
(``FROM /*x*/ secret``) while the comment-laden SQL still reaches the database.
"""
from __future__ import annotations

import re

from dbaide.models import ValidationIssue, ValidationResult
from dbaide.validation.sql_cleanup import (
    normalize_table_ref as _normalize_ref,
    table_references as _table_refs,
)


class TableScopeGuard:
    """Enforce an optional per-connection table allow/deny list. Stateless: it
    depends only on the configured scope, never on prior-turn disclosure."""

    def __init__(self, *, allow: list[str] | None = None, deny: list[str] | None = None) -> None:
        self.allow = {_norm(t) for t in (allow or []) if str(t).strip()}
        self.deny = {_norm(t) for t in (deny or []) if str(t).strip()}

    @property
    def active(self) -> bool:
        return bool(self.allow or self.deny)

    def validate(self, sql: str) -> ValidationResult:
        if not self.active:
            return ValidationResult(ok=True, normalized_sql=sql)
        cte_names = {c.lower() for c in _cte_names(sql)}
        issues: list[ValidationIssue] = []
        for ref in _table_refs(sql):
            low = ref.lower()
            bare = _bare_identifier(ref).lower()
            if bare in cte_names or low in cte_names:
                continue  # CTE alias, not a real table
            if self.deny and (low in self.deny or bare in self.deny):
                issues.append(ValidationIssue("TABLE_DENIED", f"Table not permitted by connection scope: {ref}"))
                continue
            if self.allow and not (low in self.allow or bare in self.allow):
                issues.append(ValidationIssue("TABLE_OUT_OF_SCOPE", f"Table outside connection's allowed scope: {ref}"))
        return ValidationResult(ok=not issues, issues=issues, normalized_sql=sql)

    def allows_table(self, table: str, database: str = "") -> tuple[bool, str]:
        """Check a single (database, table) target against the scope. Returns
        (ok, reason). Used by direct table-access tools (sample_rows / column_stats /
        profile_table / describe_table) so the scope can't be bypassed by going around
        execute_sql. No-op (always ok) when no scope is configured."""
        if not self.active:
            return True, ""
        refs = {_norm(table), _norm(_bare_identifier(table))}
        if database:
            refs.add(_norm(f"{database}.{table}"))
        refs.discard("")
        if self.deny and (self.deny & refs):
            return False, f"Table not permitted by connection scope: {table}"
        if self.allow and not (self.allow & refs):
            return False, f"Table outside connection's allowed scope: {table}"
        return True, ""


def _norm(value: str) -> str:
    return _normalize_ref(str(value)).lower()


def _cte_names(sql: str) -> list[str]:
    text = sql.strip()
    if not text[:4].lower() == "with":
        return []
    names: list[str] = []
    pos = 4
    depth = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if depth == 0:
            match = re.match(r"(?:recursive\s+)?([A-Za-z_][\w$]*|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])\s*(?:\([^)]*\))?\s+as\s*\(", text[pos:], re.I)
            if not match:
                break
            names.append(_bare_identifier(match.group(1)))
            pos += match.end()
            depth = 1
            continue
        ch = text[pos]
        if ch in ("'", "$$"[0]):
            if text[pos: pos + 2] == "$$":
                end = text.find("$$", pos + 2)
                pos = (end + 2) if end != -1 else len(text)
                continue
            if ch == "'":
                pos += 1
                while pos < len(text):
                    if text[pos] == "'" and (pos + 1 >= len(text) or text[pos + 1] != "'"):
                        pos += 1
                        break
                    if text[pos] == "'" and pos + 1 < len(text) and text[pos + 1] == "'":
                        pos += 2
                        continue
                    pos += 1
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                pos += 1
                while pos < len(text) and text[pos].isspace():
                    pos += 1
                if pos < len(text) and text[pos] == ",":
                    pos += 1
                    continue
                break
        pos += 1
    return names


def _bare_identifier(value: str) -> str:
    parts = _normalize_ref(value).split(".")
    return parts[len(parts) - 1] if parts else ""
