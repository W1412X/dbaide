"""Table rendering and export utilities for DBAide."""
from __future__ import annotations

import csv
import io
import json
import math
from decimal import Decimal
from typing import Any

from dbaide.adapters.base import quote_identifier


def export_json(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Export rows as a pretty-printed JSON array of objects."""
    if not rows:
        return "[]"
    cols = columns or list(rows[0].keys())
    data = [{c: row.get(c) for c in cols} for row in rows]
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _sql_literal(value: Any, *, dialect: str = "generic") -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    # Decimal is what most drivers return for NUMERIC/DECIMAL columns; it is a number
    # and must be emitted UNQUOTED (a quoted '123.45' is a string literal, which can
    # fail to insert under strict typing). NaN/Inf have no SQL literal → NULL.
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            return "NULL"
        return str(value)
    s = str(value).replace("'", "''")
    # Backslash handling is dialect-specific:
    # - MySQL/MariaDB treat backslash as an escape inside '' literals, so a literal
    #   backslash MUST be doubled (and a trailing backslash would otherwise escape the
    #   closing quote).
    # - PostgreSQL with standard_conforming_strings (the default) and SQLite treat
    #   backslash LITERALLY, so doubling there would corrupt the value (one → two). For
    #   Postgres we emit an E'' escape string when a backslash is present so the literal
    #   is unambiguous regardless of standard_conforming_strings.
    # - Other/generic dialects: standard SQL, backslash is literal.
    if dialect in ("mysql", "mariadb"):
        return "'" + s.replace("\\", "\\\\") + "'"
    if dialect in ("postgres", "postgresql") and "\\" in s:
        return "E'" + s.replace("\\", "\\\\") + "'"
    return "'" + s + "'"


def export_insert(rows: list[dict[str, Any]], columns: list[str] | None = None,
                  table: str = "table", dialect: str = "generic") -> str:
    """Export rows as INSERT statements (one per row)."""
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    table_name = quote_identifier(table, dialect)
    col_list = ", ".join(quote_identifier(col, dialect) for col in cols)
    return "\n".join(
        f"INSERT INTO {table_name} ({col_list}) VALUES (" + ", ".join(_sql_literal(row.get(c), dialect=dialect) for c in cols) + ");"
        for row in rows
    )


def export_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Export query result rows as CSV string."""
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=cols, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_value(v) for k, v in row.items() if k in cols})
    return output.getvalue()


def md_escape_cell(text: str) -> str:
    """Make a value safe inside a Markdown table cell: escape the column separator and
    collapse newlines (either would otherwise break the table — a raw `|` splits the
    row into extra columns, a newline ends the row early)."""
    return (str(text).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>"))


# Backwards-compatible private alias.
_md_escape = md_escape_cell


def export_markdown_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Export query result rows as Markdown table."""
    if not rows:
        return "(empty)"
    cols = columns or list(rows[0].keys())
    lines = []
    lines.append("| " + " | ".join(_md_escape(c) for c in cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in rows:
        values = [_md_escape(_format_cell(row.get(c))) for c in cols]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _cell_one_line(value: Any) -> str:
    """Cell text for a fixed-width text table: collapse newlines/tabs to spaces so a
    multi-line value can't inject a line break mid-row and misalign the columns."""
    text = _format_cell(value)
    if "\n" in text or "\r" in text or "\t" in text:
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return text


def format_result_text(rows: list[dict[str, Any]], columns: list[str] | None = None, max_rows: int = 100) -> str:
    """Format query result as readable text table."""
    if not rows:
        return "(empty)"
    cols = columns or list(rows[0].keys())
    display_rows = rows[:max_rows]

    # Calculate column widths
    widths = {}
    for c in cols:
        widths[c] = max(len(str(c)), max((len(_cell_one_line(row.get(c))) for row in display_rows), default=0))
        widths[c] = min(widths[c], 50)  # Cap width

    lines = []
    # Header
    lines.append(" | ".join(str(c).ljust(widths[c]) for c in cols))
    lines.append("-+-".join("-" * widths[c] for c in cols))
    # Rows
    for row in display_rows:
        lines.append(" | ".join(_cell_one_line(row.get(c))[:widths[c]].ljust(widths[c]) for c in cols))

    if len(rows) > max_rows:
        lines.append(f"\n... showing {max_rows} of {len(rows)} rows")

    return "\n".join(lines)


def _format_cell(value: Any) -> str:
    """Format a cell value for display."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if len(s) > 100:
        return s[:97] + "..."
    return s


def _csv_value(value: Any) -> str:
    """Format a value for CSV export. NULL is rendered literally so
    it round-trips distinctly from empty string."""
    if value is None:
        return "NULL"
    return str(value)
