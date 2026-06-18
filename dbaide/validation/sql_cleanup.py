"""Shared SQL pre-processing for table-reference extraction.

Several places in the codebase extract table names by scanning for ``FROM``
and ``JOIN`` keywords.  Standard SQL functions such as ``EXTRACT(YEAR FROM
col)``, ``TRIM(' ' FROM col)``, and ``SUBSTRING(col FROM 3 FOR 5)`` use the
``FROM`` keyword *inside* their argument list — which the regex scanners
would otherwise mistake for a table reference.

This module provides a single ``strip_function_from_keywords`` helper so that
every extraction site can share the same cleaning logic.
"""

from __future__ import annotations

import re

# Functions whose argument list uses the ``FROM`` keyword in standard SQL.
# EXTRACT(field FROM expr)
# TRIM([chars] FROM expr)
# SUBSTRING(expr FROM start [FOR length])  — SQL-standard two-arg form
#
# The regex replaces "FUNC(...FROM" with "FUNC(" so that the subsequent
# FROM/JOIN scanner only sees real table-level FROM keywords.
# ``[^)]*?`` (non-greedy, no close-paren) ensures we match the closest FROM
# inside the function args without crossing a closing parenthesis.
_FUNCTION_FROM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bEXTRACT\s*\([^)]*?\bFROM\b", re.I),
    re.compile(r"\bTRIM\s*\([^)]*?\bFROM\b", re.I),
    re.compile(r"\bSUBSTRING\s*\([^)]*?\bFROM\b", re.I),
]


def strip_function_from_keywords(sql: str) -> str:
    """Return *sql* with ``FROM`` inside known function calls removed.

    The result is **only** suitable for table-reference extraction — it is NOT
    valid SQL and must never be sent to a database.
    """
    cleaned = sql
    for pat in _FUNCTION_FROM_PATTERNS:
        # Replace e.g. "EXTRACT(YEAR FROM" → "EXTRACT("
        # The pattern captures up to and including FROM but not past the
        # closing paren, so we can surgically remove the interior text.
        cleaned = pat.sub(lambda m: m.group(0)[:m.group(0).index("(")] + "(", cleaned)
    return cleaned


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def blank_strings_and_comments(sql: str) -> str:
    """Replace comments and single-quoted/dollar-quoted string literals with
    spaces of equal length, preserving token boundaries and offsets.

    Comments are replaced with SPACES (not removed) so a comment between a
    keyword and an identifier — e.g. ``FROM /*x*/ secret`` — still leaves a
    visible ``FROM   secret`` for the table-reference scanner. Removing them
    instead would let a comment hide a table reference from the schema guard
    while the comment-laden SQL still reaches the database (a disclosure-boundary
    bypass). Double-quoted/backtick identifiers are KEPT (they may be quoted
    table names the scanner must see). NOT valid SQL — extraction only.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":                       # line comment
            j = sql.find("\n", i + 2)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if ch == "/" and nxt == "*":                       # block comment
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i)); i = j; continue
        if ch == "$":                                      # dollar-quoted string
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                j = n if j < 0 else j + len(tag)
                out.append(" " * (j - i)); i = j; continue
        if ch == "'":                                      # single-quoted literal
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2; continue
                    j += 1; break
                j += 1
            out.append(" " * (j - i)); i = j; continue
        if ch in ('"', "`"):                               # quoted identifier — keep
            j = i + 1
            while j < n and sql[j] != ch:
                j += 1
            j = min(j + 1, n)
            out.append(sql[i:j]); i = j; continue
        out.append(ch)
        i += 1
    return "".join(out)


# ── Table-reference extraction (shared by the scope guard, risk gate, plan, trace) ──

_TABLES_END_KEYWORDS = frozenset({
    "where", "group", "order", "having", "union", "intersect", "except",
    "limit", "offset", "window", "qualify", "fetch", "for",
    "join", "inner", "left", "right", "full", "cross", "natural", "on", "using",
})

# Unquoted branch uses [^\W\d] (Unicode letter/underscore, not a digit) as the
# start char so CJK-named tables (e.g. 订单) are extracted — important because
# TableScopeGuard relies on this for allow/deny enforcement.
_REF_IDENT = r"(?:[^\W\d][\w$]*|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])"
_REF_QUALIFIED = rf"{_REF_IDENT}(?:\s*\.\s*{_REF_IDENT})*"
_REF_LEAD = re.compile(rf"^\s*({_REF_QUALIFIED})")
_REF_JOIN = re.compile(rf"\bjoin\s+({_REF_QUALIFIED})", re.I)
_REF_FROM = re.compile(r"\bfrom\b", re.I)


def strip_identifier_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0], text[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return text[1:-1]
    return text


def normalize_table_ref(value: str) -> str:
    """Strip identifier quotes from each dotted part: `db`.`t` → db.t."""
    parts = [part.strip() for part in re.split(r"\s*\.\s*", value)]
    return ".".join(strip_identifier_quotes(part) for part in parts if part)


def _from_region(text: str, start: int) -> str:
    """Text after a top-level FROM up to the next clause keyword / enclosing ')'.
    Paren- and quote-aware so a subquery's own commas/keywords don't end the list."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "`", "["):
            close = "]" if ch == "[" else ch
            j = text.find(close, i + 1)
            i = (j + 1) if j != -1 else n
        elif ch == "(":
            depth += 1
            i += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
            i += 1
        elif depth == 0 and ch == ";":
            break
        elif depth == 0 and (ch.isalpha() or ch == "_"):
            # ch.isalpha() is True for CJK letters too, so consume a full Unicode
            # word (\w, not [A-Za-z_]) — an ASCII-only match would be None here and
            # crash. CJK words are never clause keywords, so they just advance us.
            word = re.match(r"\w+", text[i:]).group(0)
            if word.lower() in _TABLES_END_KEYWORDS:
                break
            i += len(word)
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


def table_references(sql: str) -> list[str]:
    """Extract referenced table names from a query (normalized, quote-stripped).

    Handles ``JOIN t`` and the old-style comma list ``FROM a, b, c`` (every table,
    not just the first), is paren/quote-aware (subquery derived tables are skipped —
    their inner FROM is matched separately), and blanks strings/comments so a literal
    or comment can't smuggle or hide a reference. Order-preserving, de-duplicated.
    """
    cleaned = strip_function_from_keywords(blank_strings_and_comments(sql))
    refs: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        ref = normalize_table_ref(raw.strip())
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)

    for match in _REF_JOIN.finditer(cleaned):
        _add(match.group(1))
    for match in _REF_FROM.finditer(cleaned):
        region = _from_region(cleaned, match.end())
        for item in _split_top_level_commas(region):
            stripped = item.strip()
            if not stripped or stripped.startswith("("):
                continue
            lead = _REF_LEAD.match(stripped)
            if lead:
                _add(lead.group(1))
    return refs
