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
