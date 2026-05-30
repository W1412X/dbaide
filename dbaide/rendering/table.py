"""Table rendering and export utilities for DBAide."""
from __future__ import annotations

import csv
import io
from typing import Any


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


def export_markdown_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Export query result rows as Markdown table."""
    if not rows:
        return "(empty)"
    cols = columns or list(rows[0].keys())
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in rows:
        values = [_format_cell(row.get(c)) for c in cols]
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
    """Format a value for CSV export."""
    if value is None:
        return ""
    return str(value)


def infer_column_alignment(column_name: str, values: list[Any]) -> str:
    """Infer column alignment from values."""
    numeric_count = 0
    for v in values[:20]:
        if v is None:
            continue
        try:
            float(str(v))
            numeric_count += 1
        except (ValueError, TypeError):
            pass
    if numeric_count > len(values[:20]) * 0.7:
        return "right"
    return "left"
