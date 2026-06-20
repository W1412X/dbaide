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
        structured, changed = _truncate_json_string(value, max_chars=max_chars)
        if structured is not None:
            return structured, changed
        return _truncate_text(value, max_chars=max_chars)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).hex()
        shortened, changed = _truncate_text(text, max_chars=max_chars)
        return f"<bytes hex:{shortened}>", changed or len(text) > max_chars
    if isinstance(value, (list, tuple, dict)):
        return _truncate_json_like(value, max_chars=max_chars)
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
    note = f"...[cell truncated, {omitted} chars omitted]..."
    if max_chars <= len(note) + 12:
        head = max(1, max_chars - len(note))
        return text[:head] + note, True
    remaining = max_chars - len(note)
    head = max(12, int(remaining * 0.72))
    tail = max(8, remaining - head)
    if head + tail > remaining:
        tail = max(0, remaining - head)
    return text[:head] + note + (text[-tail:] if tail else ""), True


def _truncate_json_string(text: str, *, max_chars: int) -> tuple[str | None, bool]:
    raw = str(text or "")
    stripped = raw.strip()
    if len(stripped) < 2 or stripped[0] not in "{[" or stripped[-1] not in "}]":
        return None, False
    try:
        payload = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, False
    if not isinstance(payload, (dict, list)):
        return None, False
    preview, changed = _build_json_preview(payload, max_chars=max_chars, as_text=True)
    return preview, changed or preview != raw


def _truncate_json_like(value: Any, *, max_chars: int) -> tuple[Any, bool]:
    return _build_json_preview(value, max_chars=max_chars, as_text=False)


def _build_json_preview(value: Any, *, max_chars: int, as_text: bool) -> tuple[Any, bool]:
    configs = [
        {"depth": 4, "items": 8, "string_chars": max(32, min(180, max_chars // 3))},
        {"depth": 3, "items": 6, "string_chars": max(28, min(120, max_chars // 4))},
        {"depth": 2, "items": 4, "string_chars": max(24, min(80, max_chars // 5))},
        {"depth": 1, "items": 3, "string_chars": max(20, min(56, max_chars // 6))},
    ]
    last_candidate: Any = value
    last_text = json.dumps(value, ensure_ascii=False, default=str)
    changed = False
    for cfg in configs:
        candidate, candidate_changed = _shrink_json_value(
            value,
            max_depth=cfg["depth"],
            max_items=cfg["items"],
            max_string_chars=cfg["string_chars"],
        )
        text = json.dumps(candidate, ensure_ascii=False, default=str)
        last_candidate = candidate
        last_text = text
        changed = candidate_changed
        if len(text) <= max_chars:
            return (text if as_text else candidate), changed
    shortened, hard_changed = _truncate_text(last_text, max_chars=max_chars)
    return shortened, True if (changed or hard_changed) else False


def _shrink_json_value(
    value: Any,
    *,
    max_depth: int,
    max_items: int,
    max_string_chars: int,
) -> tuple[Any, bool]:
    if value is None or isinstance(value, (int, float, bool)):
        return value, False
    if isinstance(value, str):
        return _truncate_text(value, max_chars=max_string_chars)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).hex()
        shortened, changed = _truncate_text(text, max_chars=max_string_chars)
        return f"<bytes hex:{shortened}>", changed or len(text) > max_string_chars
    if max_depth <= 0:
        if isinstance(value, dict):
            return f"...[{len(value)} key(s) collapsed]", True
        if isinstance(value, (list, tuple)):
            return f"...[{len(value)} item(s) collapsed]", True
        return _truncate_text(str(value), max_chars=max_string_chars)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        changed = False
        items = list(value.items())
        for key, child in items[:max_items]:
            child_preview, child_changed = _shrink_json_value(
                child,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            out[str(key)] = child_preview
            changed = changed or child_changed
        omitted = len(items) - min(len(items), max_items)
        if omitted > 0:
            out["..."] = f"{omitted} more key(s)"
            changed = True
        return out, changed
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        changed = False
        for child in list(value)[:max_items]:
            child_preview, child_changed = _shrink_json_value(
                child,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            out.append(child_preview)
            changed = changed or child_changed
        omitted = len(value) - min(len(value), max_items)
        if omitted > 0:
            out.append(f"...[{omitted} more item(s)]")
            changed = True
        return out, changed
    return _truncate_text(str(value), max_chars=max_string_chars)
