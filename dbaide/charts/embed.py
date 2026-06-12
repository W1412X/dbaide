"""Inline chart placeholders inside assistant Markdown answers."""

from __future__ import annotations

import re
from typing import Any, Literal

# {{chart:chart:1}} or {{chart:1}}; also ![caption](chart:1) for LLM-friendly syntax.
CHART_EMBED_RE = re.compile(
    r"\{\{chart:(?P<brace_id>chart:\d+|\d+)\}\}"
    r"|!\[[^\]]*\]\((?P<link_id>chart:\d+)\)",
    re.IGNORECASE,
)


def normalize_chart_id(raw: str) -> str:
    """Return ``chart:N`` from ``chart:N``, ``N``, or empty string."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("chart:"):
        return text
    if text.isdigit():
        return f"chart:{text}"
    return text


def split_answer_with_charts(
    answer: str,
    charts: list[dict[str, Any]] | None,
) -> list[tuple[Literal["md", "chart"], Any]]:
    """Split *answer* into alternating markdown text and chart spec dicts.

    Placeholders reference ``chart_id`` values from *charts*. Charts that were
    rendered but never referenced are appended at the end (backward compatible).
    """
    text = str(answer or "")
    chart_list = [dict(c) for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
    by_id = {str(c["chart_id"]): c for c in chart_list}
    if not text.strip():
        return [("chart", c) for c in chart_list]

    segments: list[tuple[Literal["md", "chart"], Any]] = []
    referenced: set[str] = set()
    last = 0
    for match in CHART_EMBED_RE.finditer(text):
        if match.start() > last:
            chunk = text[last : match.start()]
            if chunk.strip():
                segments.append(("md", chunk))
        chart_id = normalize_chart_id(match.group("brace_id") or match.group("link_id") or "")
        spec = by_id.get(chart_id)
        if spec is not None:
            segments.append(("chart", spec))
            referenced.add(chart_id)
        last = match.end()

    if last < len(text):
        tail = text[last:]
        if tail.strip():
            segments.append(("md", tail))

    for spec in chart_list:
        cid = str(spec.get("chart_id") or "")
        if cid and cid not in referenced:
            segments.append(("chart", spec))

    return segments
