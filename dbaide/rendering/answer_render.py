"""Shared answer document HTML builder for GUI WebEngine and export."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dbaide.rendering.answer_page import render_answer_page_html
from dbaide.rendering.compose import compose_blocks

# Headless/tests fallback — matches desktop dark ``Theme`` tokens.
_DARK_PALETTE: dict[str, Any] = {
    "BG": "#07080a",
    "TEXT": "#eef1f5",
    "TEXT_2": "#b7bec9",
    "MUTED": "#737b89",
    "BORDER_SOFT": "#1b2026",
    "CODE_BG": "#090b0f",
    "PANEL": "#111419",
    "PANEL_2": "#151922",
    "BLUE": "#67a7ff",
    "ACCENT": "#3b82f6",
    "GREEN": "#55c985",
    "YELLOW": "#e9c46a",
    "RED": "#ff6b6b",
}


def theme_payload_from_palette(
    palette: Mapping[str, Any],
    *,
    background: str | None = None,
) -> dict[str, Any]:
    """Build answer/chart theme tokens from a desktop ``Theme``-like mapping."""
    bg = str(background or palette.get("BG") or palette.get("bg") or "#07080a")
    return {
        "text": str(palette.get("TEXT") or "#eef1f5"),
        "text2": str(palette.get("TEXT_2") or palette.get("text2") or "#b7bec9"),
        "muted": str(palette.get("MUTED") or "#737b89"),
        "border": str(palette.get("BORDER_SOFT") or palette.get("border") or "#1b2026"),
        "codeBg": str(palette.get("CODE_BG") or palette.get("codeBg") or "#090b0f"),
        "panel": str(palette.get("PANEL") or palette.get("panel") or "#111419"),
        "panel2": str(palette.get("PANEL_2") or palette.get("panel2") or "#151922"),
        "link": str(palette.get("BLUE") or palette.get("link") or "#67a7ff"),
        "bg": bg,
        "colors": [
            str(palette.get("ACCENT") or "#3b82f6"),
            str(palette.get("GREEN") or "#55c985"),
            "#8b5cf6",
            str(palette.get("BLUE") or "#67a7ff"),
            "#14b8a6",
            str(palette.get("YELLOW") or "#e9c46a"),
            str(palette.get("RED") or "#ff6b6b"),
            "#f97316",
        ],
        "chartInteractive": bool(palette.get("chartInteractive", False)),
    }


def with_chart_interactive(theme: Mapping[str, Any], *, interactive: bool) -> dict[str, Any]:
    """Return a theme copy with chart zoom/pan controls enabled or disabled."""
    out = dict(theme)
    out["chartInteractive"] = bool(interactive)
    return out


DEFAULT_EMBEDDED_PADDING = "0 2px 0 0"
DEFAULT_EXPORT_PADDING = "16px 20px 32px 20px"


def format_root_padding(top: int, right: int, bottom: int, left: int) -> str:
    """Build a CSS padding value for the answer document root."""
    values = [max(0, int(v)) for v in (top, right, bottom, left)]
    return f"{values[0]}px {values[1]}px {values[2]}px {values[3]}px"


def default_answer_theme(*, background: str | None = None) -> dict[str, Any]:
    """Default dark theme for headless rendering (tests, CLI)."""
    return theme_payload_from_palette(_DARK_PALETTE, background=background)


def build_answer_document_html(
    answer: str,
    charts: list[dict[str, Any]] | None = None,
    *,
    theme: Mapping[str, Any] | None = None,
    marked_src: str | None = None,
    hljs_src: str | None = None,
    echarts_src: str | None = None,
    document_title: str = "",
    standalone: bool = False,
    root_padding: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Compose *answer* + *charts* and render the unified answer HTML page.

    Returns ``(html, blocks)``. ``standalone=True`` adds a document title and
    enables page scrolling for saved HTML files; content layout matches the
    embedded WebEngine view.
    """
    theme_map = dict(theme or default_answer_theme())
    body = str(answer or "")
    chart_list = [dict(c) for c in (charts or []) if isinstance(c, dict)]
    blocks = compose_blocks(body, chart_list, theme=theme_map)
    html = render_answer_page_html(
        blocks,
        theme=theme_map,
        marked_src=marked_src,
        hljs_src=hljs_src,
        echarts_src=echarts_src,
        document_title=document_title,
        for_export=standalone,
        root_padding=root_padding,
    )
    return html, blocks
