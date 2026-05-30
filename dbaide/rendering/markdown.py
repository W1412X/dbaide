"""Safe Markdown rendering for DBAide."""
from __future__ import annotations

import re
from typing import Any

from dbaide.rendering.sanitize import escape_user_text, sanitize_markdown_html


def render_markdown_safe(text: str) -> str:
    """Render Markdown to safe HTML.

    - Escapes raw HTML in user text
    - Supports basic Markdown: bold, italic, code, lists, headers, links
    - Sanitizes output to prevent XSS
    - Code blocks get special treatment for SQL highlighting
    """
    if not text:
        return ""

    # First escape all HTML in the source
    safe = escape_user_text(str(text))

    # Process code blocks first (fenced with ```)
    safe = _process_code_blocks(safe)

    # Process inline elements
    safe = _process_inline(safe)

    # Protect fenced code blocks (already <pre>) from paragraph wrapping
    safe, html_blocks = _isolate_html_blocks(safe)
    safe = _process_blocks(safe)
    safe = _restore_html_blocks(safe, html_blocks)

    # Final sanitization
    safe = sanitize_markdown_html(safe)

    return safe


_BLOCK_TOKEN = re.compile(r"@@HTMLBLOCK(\d+)@@")


def _isolate_html_blocks(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []
    pattern = re.compile(r"<pre[^>]*>.*?</pre>", re.S | re.I)

    def repl(match: re.Match[str]) -> str:
        blocks.append(match.group(0))
        return f"@@HTMLBLOCK{len(blocks) - 1}@@"

    return pattern.sub(repl, text), blocks


def _restore_html_blocks(text: str, blocks: list[str]) -> str:
    for index, block in enumerate(blocks):
        token = f"@@HTMLBLOCK{index}@@"
        text = text.replace(f"<p>{token}</p>", block)
        text = text.replace(token, block)
    return text


def _process_code_blocks(text: str) -> str:
    """Process fenced code blocks."""
    def replace_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        lang_attr = f' data-lang="{lang}"' if lang else ""
        return f'<pre{lang_attr}><code>{code}</code></pre>'

    return re.sub(r'```(\w*)\n(.*?)```', replace_block, text, flags=re.S)


def _process_inline(text: str) -> str:
    """Process inline Markdown elements."""
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)

    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<em>\1</em>', text)

    # Inline code: `code`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # Links: [text](url) - only allow http/https
    text = re.sub(
        r'\[([^\]]+)\]\((https?://[^)]+)\)',
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )

    return text


def _process_blocks(text: str) -> str:
    """Process block Markdown elements."""
    lines = text.split('\n')
    result = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Headers
        if stripped.startswith('### '):
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(f'<h3>{stripped[4:]}</h3>')
        elif stripped.startswith('## '):
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(f'<h2>{stripped[3:]}</h2>')
        elif stripped.startswith('# '):
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(f'<h1>{stripped[2:]}</h1>')

        # Unordered list
        elif stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                result.append('<ul>')
                in_list = True
            result.append(f'<li>{stripped[2:]}</li>')

        # Ordered list
        elif re.match(r'^\d+\.\s', stripped):
            if not in_list:
                result.append('<ol>')
                in_list = True
            content = re.sub(r'^\d+\.\s', '', stripped)
            result.append(f'<li>{content}</li>')

        # Empty line
        elif not stripped:
            if in_list:
                result.append('</ul>' if result[-1].startswith('<li>') else '</ol>')
                in_list = False
            result.append('')

        # Preserved HTML blocks (fenced code converted to <pre>)
        elif _BLOCK_TOKEN.fullmatch(stripped):
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(stripped)

        # Regular paragraph
        else:
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(f'<p>{stripped}</p>')

    if in_list:
        result.append('</ul>')

    return '\n'.join(result)


def format_answer_card(
    *,
    summary: str = "",
    sql: str = "",
    result_text: str = "",
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
    elapsed_ms: float = 0.0,
) -> str:
    """Format an answer card as Markdown."""
    parts = []

    if summary:
        parts.append(summary)
        parts.append("")

    if sql:
        parts.append("**SQL:**")
        parts.append(f"```sql\n{sql}\n```")
        parts.append("")

    if result_text:
        parts.append("**Result:**")
        parts.append(result_text)
        parts.append("")

    if assumptions:
        parts.append("**Assumptions:**")
        for a in assumptions:
            parts.append(f"- {a}")
        parts.append("")

    if warnings:
        parts.append("**Warnings:**")
        for w in warnings:
            parts.append(f"- {w}")
        parts.append("")

    if elapsed_ms > 0:
        parts.append(f"*Completed in {elapsed_ms:.0f}ms*")

    return '\n'.join(parts)
