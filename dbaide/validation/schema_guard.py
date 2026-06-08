from __future__ import annotations

import re

from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ValidationIssue, ValidationResult
from dbaide.validation.sql_cleanup import strip_function_from_keywords


class SchemaGuard:
    """
    Best-effort guard that blocks obvious table/column hallucinations against disclosed schema.
    It is intentionally conservative and never claims full SQL parsing.
    """

    def validate(self, sql: str, context: DisclosureContext) -> ValidationResult:
        issues: list[ValidationIssue] = []
        exact_refs, unique_bare_refs = _known_table_refs(context)
        if not exact_refs and not unique_bare_refs:
            return ValidationResult(ok=True, normalized_sql=sql)
        cte_names = set(_cte_names(sql))
        for ref in _table_refs(sql):
            bare = _bare_identifier(ref)
            if bare in cte_names or ref in cte_names:
                continue
            if ref in exact_refs:
                continue
            if "." not in ref and ref in unique_bare_refs:
                continue
            if bare in cte_names:
                continue
            if ref not in exact_refs:
                issues.append(ValidationIssue("UNKNOWN_TABLE", f"SQL references undisclosed or unknown table: {ref}"))
        return ValidationResult(ok=not issues, issues=issues, normalized_sql=sql)


def _table_refs(sql: str) -> list[str]:
    refs: list[str] = []
    # Strip the FROM keyword inside SQL functions (EXTRACT, TRIM, SUBSTRING)
    # so the regex below doesn't mistake column names for table references.
    cleaned = strip_function_from_keywords(sql)
    ident = r"(?:[A-Za-z_][\w$]*|`[^`]+`|\"[^\"]+\"|\[[^\]]+\])"
    pattern = re.compile(rf"\b(?:from|join)\s+({ident}(?:\s*\.\s*{ident})*)", re.I)
    for match in pattern.finditer(cleaned):
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
        # Skip string literals so parentheses inside them don't affect depth.
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


def _known_table_refs(context: DisclosureContext) -> tuple[set[str], set[str]]:
    exact_refs: set[str] = set()
    bare_counts: dict[str, int] = {}
    for ref, entry in context.tables.items():
        table = _normalize_ref(entry.table.name)
        database = _normalize_ref(entry.database or entry.table.schema or "")
        stored_ref = _normalize_ref(ref)
        if stored_ref:
            exact_refs.add(stored_ref)
        if table:
            bare_counts[table] = bare_counts.get(table, 0) + 1
        if database and table:
            exact_refs.add(f"{database}.{table}")
    unique_bare_refs = {name for name, count in bare_counts.items() if count == 1}
    return exact_refs, unique_bare_refs


def _strip_identifier_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0], text[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return text[1:-1]
    return text
