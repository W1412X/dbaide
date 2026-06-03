"""Shared stylesheet for rendered Markdown shown in QTextBrowser views.

Qt's rich-text engine only understands a CSS 2.1 subset, and it applies a
document's *default* stylesheet far more reliably than an inline ``<style>`` block
(inline styles drop selectors like inline ``code`` backgrounds). It also ignores
``border-left`` / ``border-radius`` on non-table blocks and the ``background``
shorthand — so blockquotes use a ``background-color`` tint as their cue and code
uses ``background-color`` (not ``background``). Apply via
``doc.setDefaultStyleSheet(markdown_stylesheet())``.
"""

from __future__ import annotations

from dbaide.desktop.theme import Theme


def markdown_stylesheet() -> str:
    t = Theme
    return (
        f"p {{ margin: 5px 0; }}"
        f"h1 {{ font-size: 18px; font-weight: 700; margin: 12px 0 6px; }}"
        f"h2 {{ font-size: 16px; font-weight: 700; margin: 10px 0 6px; }}"
        f"h3 {{ font-size: 14px; font-weight: 600; margin: 9px 0 4px; }}"
        f"h4 {{ font-size: 13px; font-weight: 600; margin: 8px 0 4px; }}"
        f"h5, h6 {{ font-size: 12px; font-weight: 600; margin: 8px 0 4px; color: {t.TEXT_2}; }}"
        f"ul, ol {{ margin: 4px 0; }}"
        f"li {{ margin: 2px 0; }}"
        # Blockquote: Qt ignores border-left, so a tinted panel + muted text is the
        # visual cue for a quote.
        f"blockquote {{ background-color: {t.PANEL_2}; color: {t.TEXT_2};"
        f" margin: 6px 0; padding: 6px 12px; }}"
        # Code block.
        f"pre {{ background-color: {t.CODE_BG}; padding: 10px 12px;"
        f" font-family: 'Menlo', monospace; font-size: 12px; white-space: pre-wrap; }}"
        # Inline code — background-color (NOT the `background` shorthand Qt drops).
        f"code {{ background-color: {t.CODE_BG}; font-family: 'Menlo', monospace; font-size: 12px; }}"
        f"pre code {{ background-color: transparent; }}"
        f"hr {{ border-width: 0; background-color: {t.BORDER}; }}"
        # Tables: header underline + faint row rules (no boxy grid).
        f"table.md-table {{ border-collapse: collapse; margin: 8px 0; }}"
        f"table.md-table th, table.md-table td {{ border-style: none;"
        f" border-bottom: 1px solid {t.BORDER_SOFT}; padding: 7px 14px 7px 0; }}"
        f"table.md-table th {{ color: {t.MUTED}; font-weight: 600; }}"
        f"a {{ color: {t.BLUE}; }}"
    )
