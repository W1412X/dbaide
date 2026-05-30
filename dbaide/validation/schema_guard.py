from __future__ import annotations

import re

from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ValidationIssue, ValidationResult


class SchemaGuard:
    """
    Best-effort guard that blocks obvious table/column hallucinations against disclosed schema.
    It is intentionally conservative and never claims full SQL parsing.
    """

    def validate(self, sql: str, context: DisclosureContext) -> ValidationResult:
        issues: list[ValidationIssue] = []
        known_tables = set(context.table_names()) | set(context.tables.keys())
        if not known_tables:
            return ValidationResult(ok=True, normalized_sql=sql)
        cte_names = set(_cte_names(sql))
        for ref in _table_refs(sql):
            bare = _bare_identifier(ref)
            if bare in cte_names or ref in cte_names:
                continue
            if ref not in known_tables and bare not in known_tables:
                issues.append(ValidationIssue("UNKNOWN_TABLE", f"SQL references undisclosed or unknown table: {ref}"))
        return ValidationResult(ok=not issues, issues=issues, normalized_sql=sql)


def _table_refs(sql: str) -> list[str]:
    refs: list[str] = []
    ident = r"(?:[A-Za-z_][\w$]*|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])"
    pattern = re.compile(rf"\b(?:from|join)\s+({ident}(?:\s*\.\s*{ident})*)", re.I)
    for match in pattern.finditer(sql):
        refs.append(_normalize_ref(match.group(1).strip()))
    return refs


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
    return _normalize_ref(value).split(".")[-1]


def _strip_identifier_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0], text[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return text[1:-1]
    return text
