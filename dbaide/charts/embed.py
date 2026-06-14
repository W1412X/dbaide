"""Inline chart placeholders inside assistant Markdown answers."""

from __future__ import annotations

import re
from typing import Any, Literal

# {{chart:1}} or ![caption](chart:1)
CHART_EMBED_RE = re.compile(
    r"\{\{chart:(?P<brace_id>\d+)\}\}"
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


def chart_embed_markdown(chart_id: str) -> str:
    """Canonical placeholder token for a chart id (``chart:N`` → ``{{chart:N}}``)."""
    cid = normalize_chart_id(chart_id)
    if not cid.startswith("chart:"):
        return ""
    return f"{{{{chart:{cid.split(':', 1)[1]}}}}}"


def split_answer_with_charts(
    answer: str,
    charts: list[dict[str, Any]] | None,
) -> list[tuple[Literal["md", "chart"], Any]]:
    """Split *answer* into alternating markdown text and chart spec dicts.

    Charts render only where the answer references them via ``{{chart:N}}`` or
    ``![caption](chart:N)``. Unreferenced charts are omitted.
    """
    text = str(answer or "")
    chart_list = [dict(c) for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
    by_id = {str(c["chart_id"]): c for c in chart_list}
    if not text.strip():
        return []

    segments: list[tuple[Literal["md", "chart"], Any]] = []
    last = 0
    for match in CHART_EMBED_RE.finditer(text):
        if match.start() > last:
            chunk = text[last : match.start()]
            if chunk.strip():
                segments.append(("md", chunk))
        raw_id = match.group("brace_id") or match.group("link_id") or ""
        chart_id = normalize_chart_id(raw_id)
        spec = by_id.get(chart_id)
        if spec is not None:
            segments.append(("chart", spec))
        last = match.end()

    if last < len(text):
        tail = text[last:]
        if tail.strip():
            segments.append(("md", tail))

    return segments
