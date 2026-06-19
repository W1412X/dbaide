"""Compose assistant answers into an ephemeral AnswerDocument block list.

Persistent storage remains ``answer_markdown`` + ``charts[]`` only; this module
builds the in-memory document used by the unified HTML renderer at display time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from dbaide.charts.echarts import chart_spec_to_echarts_option
from dbaide.charts.embed import CHART_EMBED_RE, split_answer_with_charts
from dbaide.charts.layout import estimate_chart_height_from_spec
from dbaide.charts.spec import chart_spec_from_dict

SCHEMA_VERSION = 1


def compose_blocks(
    answer: str,
    charts: list[dict[str, Any]] | None,
    *,
    theme: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build ordered answer blocks from raw markdown + chart specs.

    Chart blocks include a validated ``spec`` and a precomputed ``echarts_option``
    for the HTML layer. Nothing here is persisted.
    """
    body = str(answer or "")
    chart_list = [dict(c) for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
    has_embeds = bool(CHART_EMBED_RE.search(body))

    blocks: list[dict[str, Any]] = []
    rendered_chart_ids: set[str] = set()

    if body.strip() or has_embeds:
        segments = split_answer_with_charts(body, chart_list)
        if not segments and body.strip():
            segments = [("md", body)]
        for kind, payload in segments:
            if kind == "md":
                blocks.extend(_markdown_blocks(str(payload)))
            elif kind == "chart" and isinstance(payload, dict):
                block = _chart_block(payload, theme=theme)
                if block is not None:
                    cid = str(block.get("chart_id") or "")
                    if cid:
                        rendered_chart_ids.add(cid)
                    blocks.append(block)

    for chart in chart_list:
        cid = str(chart.get("chart_id") or "")
        if cid and cid not in rendered_chart_ids:
            block = _chart_block(chart, theme=theme)
            if block is not None:
                blocks.append(block)

    return blocks


def compose_document(
    answer: str,
    charts: list[dict[str, Any]] | None,
    *,
    theme: Mapping[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap :func:`compose_blocks` in a versioned AnswerDocument envelope."""
    doc_meta = dict(meta or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": doc_meta,
        "blocks": compose_blocks(answer, charts, theme=theme),
    }


def _markdown_blocks(source: str) -> list[dict[str, Any]]:
    text = str(source or "")
    if not text.strip():
        return []
    return [{"type": "markdown", "source": text}]


def _chart_block(
    spec_dict: dict[str, Any],
    *,
    theme: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    chart_id = str(spec_dict.get("chart_id") or "").strip()
    if not chart_id:
        return None
    try:
        chart_spec_from_dict(spec_dict)
        option = chart_spec_to_echarts_option(spec_dict, theme=theme)
        height = estimate_chart_height_from_spec(spec_dict)
    except Exception as exc:
        return {
            "type": "markdown",
            "source": f"⚠ Chart `{chart_id}` could not be rendered: {exc}",
        }
    return {
        "type": "chart",
        "chart_id": chart_id,
        "title": str(spec_dict.get("title") or "").strip(),
        "spec": dict(spec_dict),
        "echarts_option": option,
        "height": height,
    }


BlockKind = Literal["markdown", "chart"]
