"""Dependency-free SQL helpers: statement splitting + a light pretty-printer.

We deliberately avoid a heavy parser (e.g. sqlparse) to keep the packaged bundle
small. Both helpers tokenise just enough to respect string literals and comments
so ``;`` inside a quoted value or ``--`` comment is never mistaken for a
statement boundary or a keyword.
"""
from __future__ import annotations

import re

# Clauses that begin a new line at the base indent level.
_NEWLINE_KEYWORDS = (
    "select", "from", "where", "group by", "order by", "having", "limit",
    "offset", "union all", "union", "intersect", "except", "values",
    "left join", "right join", "inner join", "outer join", "cross join",
    "full join", "join", "on", "set", "returning", "with",
)
# Reserved words we uppercase when they appear as standalone tokens.
_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "like",
    "between", "group", "by", "order", "having", "limit", "offset", "as", "on",
    "join", "left", "right", "inner", "outer", "cross", "full", "union", "all",
    "intersect", "except", "distinct", "case", "when", "then", "else", "end",
    "asc", "desc", "with", "values", "insert", "into", "update", "delete",
    "set", "create", "table", "view", "index", "primary", "key", "foreign",
    "references", "default", "exists", "count", "sum", "avg", "min", "max",
    "returning", "using",
}


def _spans_to_mask(sql: str) -> list[bool]:
    """Mark each char as 'inside a string/comment' (True) so callers can skip it."""
    mask = [False] * len(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                mask[k] = True
            i = j
        elif two == "/*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                mask[k] = True
            i = j
        elif ch in ("'", '"', "`"):
            mask[i] = True
            j = i + 1
            while j < n:
                mask[j] = True
                if sql[j] == ch:
                    # doubled quote = escaped, stay inside
                    if j + 1 < n and sql[j + 1] == ch:
                        mask[j + 1] = True
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            i = j
        else:
            i += 1
    return mask


def split_statements(sql: str) -> list[tuple[int, int, str]]:
    """Split into top-level statements, ignoring ``;`` inside strings/comments.

    Returns ``(start, end, text)`` spans (over the original string) for each
    non-empty statement, so a caller can locate the statement under a cursor.
    """
    mask = _spans_to_mask(sql)
    out: list[tuple[int, int, str]] = []
    start = 0
    for i, ch in enumerate(sql):
        if ch == ";" and not mask[i]:
            text = sql[start:i].strip()
            if text:
                out.append((start, i, text))
            start = i + 1
    tail = sql[start:].strip()
    if tail:
        out.append((start, len(sql), tail))
    return out


def statement_at(sql: str, cursor: int) -> str:
    """Return the statement containing ``cursor`` (or the whole text if single)."""
    spans = split_statements(sql)
    if not spans:
        return sql.strip()
    if len(spans) == 1:
        return spans[0][2]
    for start, end, text in spans:
        if start <= cursor <= end:
            return text
    # cursor past the last ';' → the trailing statement (or last)
    return spans[-1][2]


def _tokenize(sql: str) -> list[str]:
    mask = _spans_to_mask(sql)
    tokens: list[str] = []
    i, n = 0, len(sql)
    buf = ""
    while i < n:
        if mask[i]:
            # consume the whole literal/comment span verbatim as one token
            if buf:
                tokens.append(buf)
                buf = ""
            j = i
            while j < n and mask[j]:
                j += 1
            tokens.append(sql[i:j])
            i = j
            continue
        ch = sql[i]
        if ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
        elif ch in "(),":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
            i += 1
        else:
            buf += ch
            i += 1
    if buf:
        tokens.append(buf)
    return [tk for tk in tokens if tk != ""]


def format_sql(sql: str) -> str:
    """Pretty-print one or more statements: keyword-cased, clause-per-line.

    Best-effort and conservative — it never drops content, so an unrecognised
    construct simply round-trips with normalised whitespace.
    """
    stripped = sql.strip()
    if not stripped:
        return ""
    parts = split_statements(stripped)
    if len(parts) > 1:
        return ";\n\n".join(format_sql(text) for _, _, text in parts) + ";"

    tokens = _tokenize(parts[0][2] if parts else stripped)
    if not tokens:
        return stripped

    lines: list[str] = []
    cur = ""
    depth = 0
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        low = tok.lower()
        is_literal = tok[:1] in ("'", '"', "`") or tok[:2] in ("--", "/*")

        # Look for a two-word clause keyword (e.g. "group by", "left join").
        two = ""
        if idx + 1 < len(tokens):
            two = f"{low} {tokens[idx + 1].lower()}"

        clause = None
        if not is_literal and depth == 0:
            if two in _NEWLINE_KEYWORDS:
                clause = two
            elif low in _NEWLINE_KEYWORDS:
                clause = low

        if clause and clause != "on":
            if cur.strip():
                lines.append(cur.rstrip())
            cur = clause.upper()
            idx += 2 if " " in clause else 1
            continue
        if clause == "on":  # keep ON attached, lightly indented under the JOIN
            if cur.strip():
                lines.append(cur.rstrip())
            cur = "  ON"
            idx += 1
            continue

        if tok == "(":
            depth += 1
            cur += "("
            idx += 1
            continue
        if tok == ")":
            depth = max(0, depth - 1)
            cur = cur.rstrip() + ")"
            idx += 1
            continue
        if tok == ",":
            cur = cur.rstrip() + ","
            if depth == 0:
                lines.append(cur)
                cur = "  "
            idx += 1
            continue

        piece = tok if is_literal else (tok.upper() if low in _KEYWORDS else tok)
        if cur and not cur.endswith(("(", " ")):
            cur += " "
        cur += piece
        idx += 1

    if cur.strip():
        lines.append(cur.rstrip())
    return "\n".join(line.rstrip() for line in lines)
