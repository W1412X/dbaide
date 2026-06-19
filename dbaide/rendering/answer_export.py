"""Export assistant answers as standalone HTML files."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from dbaide.rendering.answer_render import (
    DEFAULT_EXPORT_PADDING,
    build_answer_document_html,
    default_answer_theme,
)
from dbaide.rendering.vendor_scripts import CDN_ECHARTS, CDN_HLJS, CDN_MARKED


def suggest_export_filename(title: str = "", *, default: str = "dbaide-answer.html") -> str:
    """Build a safe default filename from the turn question."""
    text = " ".join(str(title or "").split()).strip()
    if not text:
        return default
    slug = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE).strip("-_")
    slug = slug[:48].strip("-_") or "answer"
    return f"{slug}.html"


def export_answer_html(
    answer: str,
    charts: list[dict[str, Any]] | None = None,
    *,
    title: str = "",
    theme: Mapping[str, Any] | None = None,
    root_padding: str | None = None,
) -> str:
    """Render a portable HTML document from raw answer markdown + chart specs.

    Uses the same compose/render path as the in-app answer view. Script URLs
    use CDN so the saved file opens correctly in an external browser.
    """
    theme_map = dict(theme or default_answer_theme())
    doc_title = " ".join(str(title or "").split()).strip() or "DBAide Answer"
    html, _blocks = build_answer_document_html(
        answer,
        charts,
        theme=theme_map,
        marked_src=CDN_MARKED,
        hljs_src=CDN_HLJS,
        echarts_src=CDN_ECHARTS,
        document_title=doc_title,
        standalone=True,
        root_padding=root_padding if root_padding is not None else DEFAULT_EXPORT_PADDING,
    )
    return html
