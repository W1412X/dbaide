"""Table rendering and export utilities for DBAide."""
from __future__ import annotations

import csv
import io
import json
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
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("'", "''")
    # A trailing backslash would escape the closing quote on any dialect.
    # MySQL/MariaDB also interpret \n, \t etc. inside literals, so doubling
    # is required there; for others it's still necessary to avoid broken SQL.
    s = s.replace("\\", "\\\\")
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


def _md_escape(text: str) -> str:
    """Make a value safe inside a Markdown table cell: escape the column separator and
    collapse newlines (either would otherwise break the table — a raw `|` splits the
    row into extra columns, a newline ends the row early)."""
    return (str(text).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>"))


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


def format_result_text(rows: list[dict[str, Any]], columns: list[str] | None = None, max_rows: int = 100) -> str:
    """Format query result as readable text table."""
    if not rows:
        return "(empty)"
    cols = columns or list(rows[0].keys())
    display_rows = rows[:max_rows]

    # Calculate column widths
    widths = {}
    for c in cols:
        widths[c] = max(len(str(c)), max((len(_format_cell(row.get(c))) for row in display_rows), default=0))
        widths[c] = min(widths[c], 50)  # Cap width

    lines = []
    # Header
    lines.append(" | ".join(str(c).ljust(widths[c]) for c in cols))
    lines.append("-+-".join("-" * widths[c] for c in cols))
    # Rows
    for row in display_rows:
        lines.append(" | ".join(_format_cell(row.get(c))[:widths[c]].ljust(widths[c]) for c in cols))

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
