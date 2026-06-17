"""Bounded table-result previews for model/tool surfaces.

The database adapter may return rows that contain very large TEXT/JSON values.
Tool outputs should preserve row/column structure while truncating oversized
cells individually, instead of truncating the whole JSON blob and losing later
columns or rows.
"""
from __future__ import annotations

import json
from typing import Any

DEFAULT_MAX_ROWS = 20
DEFAULT_MAX_CELL_CHARS = 500


def preview_rows(
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    columns: list[str] | tuple[str, ...] | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_cell_chars: int = DEFAULT_MAX_CELL_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a bounded, row-preserving preview plus truncation metadata."""
    source = list(rows or [])
    limit = max(1, int(max_rows or DEFAULT_MAX_ROWS))
    cell_limit = max(20, int(max_cell_chars or DEFAULT_MAX_CELL_CHARS))
    col_order = [str(c) for c in (columns or [])]
    preview: list[dict[str, Any]] = []
    truncated_cells = 0

    for raw_row in source[:limit]:
        if not isinstance(raw_row, dict):
            text, changed = truncate_value(raw_row, max_chars=cell_limit)
            preview.append({"value": text})
            truncated_cells += int(changed)
            continue
        keys = col_order or [str(k) for k in raw_row.keys()]
        row_out: dict[str, Any] = {}
        for key in keys:
            value, changed = truncate_value(raw_row.get(key), max_chars=cell_limit)
            row_out[key] = value
            truncated_cells += int(changed)
        # Preserve unexpected keys without disturbing the declared column order.
        for key, raw_value in raw_row.items():
            skey = str(key)
            if skey in row_out:
                continue
            value, changed = truncate_value(raw_value, max_chars=cell_limit)
            row_out[skey] = value
            truncated_cells += int(changed)
        preview.append(row_out)

    return preview, {
        "rows_returned": len(source),
        "rows_previewed": len(preview),
        "row_preview_truncated": len(source) > len(preview),
        "cell_truncated": truncated_cells > 0,
        "truncated_cells": truncated_cells,
        "max_cell_chars": cell_limit,
    }


def truncate_value(value: Any, *, max_chars: int = DEFAULT_MAX_CELL_CHARS) -> tuple[Any, bool]:
    """Truncate one cell while keeping primitive values primitive when possible."""
    if value is None or isinstance(value, (int, float, bool)):
        return value, False
    if isinstance(value, str):
        return _truncate_text(value, max_chars=max_chars)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).hex()
        shortened, changed = _truncate_text(text, max_chars=max_chars)
        return f"<bytes hex:{shortened}>", changed or len(text) > max_chars
    if isinstance(value, (list, tuple, dict)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return _truncate_text(text, max_chars=max_chars)
    return _truncate_text(str(value), max_chars=max_chars)


def bounded_json_text(value: Any, *, max_chars: int = 12000) -> str:
    """JSON text with a final hard cap for external protocols such as MCP."""
    text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    cap = max(1000, int(max_chars or 12000))
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n...[truncated from {len(text)} chars]"


def _truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    omitted = len(text) - max_chars
    return text[:max_chars] + f"...[cell truncated, {omitted} chars omitted]", True
