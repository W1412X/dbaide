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
