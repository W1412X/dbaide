"""Category label formatting for charts (dates, dense axes)."""

from __future__ import annotations

import re

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})"
)


def format_category_label(text: str, *, compact: bool = False) -> str:
    """Format a category label for display on a chart axis."""
    text = " ".join(str(text or "").split())
    if not text:
        return "—"

    m = _ISO_DATETIME.match(text)
    if m:
        y, mo, d, hh, mm = m.groups()
        if compact:
            return f"{mo}-{d} {hh}:{mm}"
        return f"{y}-{mo}-{d}"

    m = _ISO_DATE.match(text[:10])
    if m:
        y, mo, d = m.groups()
        if compact:
            return f"{mo}-{d}"
        return f"{y}-{mo}-{d}"

    if compact and len(text) > 14:
        return text[:13].rstrip() + "…"
    if len(text) > 24:
        return text[:23].rstrip() + "…"
    return text


def category_axis_layout(categories: list[str]) -> tuple[list[str], int, int]:
    """Plan axis display labels, rotation angle (degrees), and extra bottom margin."""
    raw = [str(c) for c in categories]
    n = len(raw)
    if n == 0:
        return [], 0, 0

    max_len = max(len(c) for c in raw)
    dense = n > 6 or (n > 4 and max_len > 10)
    very_dense = n > 12 or (n > 8 and max_len > 12)
    compact = dense or any(_ISO_DATE.match(c[:10]) or _ISO_DATETIME.match(c) for c in raw)

    display = [format_category_label(c, compact=compact) for c in raw]

    if very_dense:
        return display, -60, 52
    if dense:
        return display, -45, 38
    return display, 0, 8
