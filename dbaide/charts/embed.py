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


def next_available_chart_id(used: set[str]) -> str:
    """Return the next free ``chart:N`` id for a merged answer context."""
    n = 1
    while True:
        chart_id = f"chart:{n}"
        if chart_id not in used:
            return chart_id
        n += 1


def remap_chart_refs(answer: str, id_map: dict[str, str]) -> str:
    """Rewrite chart placeholders in *answer* according to ``old_id -> new_id``."""
    if not id_map:
        return answer

    def replace(match) -> str:
        raw_id = match.group("brace_id") or match.group("link_id") or ""
        old_id = normalize_chart_id(raw_id)
        new_id = id_map.get(old_id)
        if not new_id:
            return match.group(0)
        if match.group("brace_id"):
            return chart_embed_markdown(new_id)
        return match.group(0).replace(old_id, new_id)

    return CHART_EMBED_RE.sub(replace, str(answer or ""))


def merge_chart_specs(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return incoming chart specs with ids made unique against *existing*.

    The returned mapping contains only ids that changed and can be passed to
    :func:`remap_chart_refs` for the answer text produced with the incoming specs.
    """
    used = {
        normalize_chart_id(str(item.get("chart_id") or ""))
        for item in (existing or [])
        if isinstance(item, dict) and item.get("chart_id")
    }
    id_map: dict[str, str] = {}
    merged: list[dict[str, Any]] = []
    for item in (incoming or []):
        if not isinstance(item, dict):
            continue
        chart = dict(item)
        old_id = normalize_chart_id(str(chart.get("chart_id") or ""))
        new_id = old_id if old_id and old_id not in used else next_available_chart_id(used)
        used.add(new_id)
        if old_id and new_id != old_id:
            id_map[old_id] = new_id
        chart["chart_id"] = new_id
        merged.append(chart)
    return merged, id_map


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
