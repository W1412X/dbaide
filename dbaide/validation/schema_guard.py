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
from dbaide.validation.sql_cleanup import blank_strings_and_comments, strip_function_from_keywords


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


def _norm(value: str) -> str:
    return _normalize_ref(str(value)).lower()


# Keywords that end a FROM table-list (so the comma-separated list is bounded).
_FROM_END_KEYWORDS = frozenset({
    "where", "group", "order", "having", "union", "intersect", "except",
    "limit", "offset", "window", "qualify", "fetch", "for",
    "join", "inner", "left", "right", "full", "cross", "natural", "on", "using",
})

_IDENT = r"(?:[A-Za-z_][\w$]*|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])"
_QUALIFIED = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"
_LEAD_IDENT_RE = re.compile(rf"^\s*({_QUALIFIED})")


def _table_refs(sql: str) -> list[str]:
    refs: list[str] = []
    # Blank comments/strings first so a comment can't hide a table reference,
    # then strip the FROM inside SQL functions (EXTRACT/TRIM/SUBSTRING).
    cleaned = strip_function_from_keywords(blank_strings_and_comments(sql))
    # JOIN introduces exactly one table.
    for match in re.finditer(rf"\bjoin\s+({_QUALIFIED})", cleaned, re.I):
        refs.append(_normalize_ref(match.group(1).strip()))
    # FROM introduces a COMMA-SEPARATED list of tables (old-style joins:
    # ``FROM a, b, c``). Capturing only the first table let a denied/out-of-scope
    # table ride along after a comma — a scope bypass. Bound the list at the next
    # clause keyword and split top-level commas. Derived tables ``(SELECT ...)`` are
    # skipped here; their inner FROM is matched by this same loop.
    for match in re.finditer(r"\bfrom\b", cleaned, re.I):
        region = _from_region(cleaned, match.end())
        for item in _split_top_level_commas(region):
            stripped = item.strip()
            if not stripped or stripped.startswith("("):
                continue
            lead = _LEAD_IDENT_RE.match(stripped)
            if lead:
                refs.append(_normalize_ref(lead.group(1).strip()))
    return refs


def _from_region(text: str, start: int) -> str:
    """Text after a top-level FROM up to the next clause keyword / enclosing ')'.
    Paren-aware so a subquery's own WHERE/commas don't end the outer list."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "`", "["):
            # Skip a quoted identifier whole — its contents (e.g. a column named
            # "order") must not be read as a clause keyword that ends the list.
            close = "]" if ch == "[" else ch
            j = text.find(close, i + 1)
            i = (j + 1) if j != -1 else n
        elif ch == "(":
            depth += 1
            i += 1
        elif ch == ")":
            if depth == 0:
                break  # FROM lives inside a subquery; list ends at the enclosing )
            depth -= 1
            i += 1
        elif depth == 0 and ch == ";":
            break
        elif depth == 0 and (ch.isalpha() or ch == "_"):
            word = re.match(r"[A-Za-z_]+", text[i:]).group(0)
            if word.lower() in _FROM_END_KEYWORDS:
                break
            i += len(word)  # skip whole word so 'orders' isn't read as 'order'
        else:
            i += 1
    return text[start:i]


def _split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "`", "["):
            close = "]" if ch == "[" else ch
            j = text.find(close, i + 1)
            i = (j + 1) if j != -1 else n
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            items.append(text[start:i])
            start = i + 1
        i += 1
    items.append(text[start:])
    return items


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


def _normalize_ref(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*\.\s*", value)]
    return ".".join(_strip_identifier_quotes(part) for part in parts if part)


def _bare_identifier(value: str) -> str:
    parts = _normalize_ref(value).split(".")
    return parts[len(parts) - 1] if parts else ""


def _strip_identifier_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0], text[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return text[1:-1]
    return text
