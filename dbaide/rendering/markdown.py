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

    # GitHub-style pipe tables (before inline/block so rows are not wrapped in <p>)
    safe = _process_tables(safe)

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
    pattern = re.compile(r"<(?:pre|table)\b[^>]*>.*?</(?:pre|table)>", re.S | re.I)

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


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|") and stripped.count("|") >= 2:
        return True
    return stripped.count("|") >= 1 and not stripped.startswith("#")


def _is_table_separator(line: str) -> bool:
    stripped = line.strip().strip("|")
    if not stripped:
        return False
    parts = [part.strip() for part in stripped.split("|")]
    if not parts:
        return False
    return all(re.fullmatch(r":?-{3,}:?", part) for part in parts if part)


def _parse_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_table_html(header: list[str], rows: list[list[str]]) -> str:
    def _cells(values: list[str], tag: str) -> str:
        parts = []
        for value in values:
            parts.append(f"<{tag}>{_process_inline(value)}</{tag}>")
        return "".join(parts)

    thead = f"<thead><tr>{_cells(header, 'th')}</tr></thead>"
    body_rows = "".join(f"<tr>{_cells(row, 'td')}</tr>" for row in rows)
    tbody = f"<tbody>{body_rows}</tbody>" if body_rows else ""
    return f'<table class="md-table">{thead}{tbody}</table>'


def _process_tables(text: str) -> str:
    lines = text.split("\n")
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            _looks_like_table_row(line)
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            header = _parse_table_row(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and _looks_like_table_row(lines[index]):
                rows.append(_parse_table_row(lines[index]))
                index += 1
            result.append(_render_table_html(header, rows))
            continue
        result.append(line)
        index += 1
    return "\n".join(result)


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
    list_tag = ""  # "ul" | "ol" | "" — track the open list type to close it correctly

    for line in lines:
        stripped = line.strip()

        # Headers
        if stripped.startswith('### '):
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append(f'<h3>{stripped[4:]}</h3>')
        elif stripped.startswith('## '):
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append(f'<h2>{stripped[3:]}</h2>')
        elif stripped.startswith('# '):
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append(f'<h1>{stripped[2:]}</h1>')

        # Unordered list
        elif stripped.startswith('- ') or stripped.startswith('* '):
            if list_tag != "ul":
                if list_tag:
                    result.append(f'</{list_tag}>')
                result.append('<ul>')
                list_tag = "ul"
            result.append(f'<li>{stripped[2:]}</li>')

        # Ordered list
        elif re.match(r'^\d+\.\s', stripped):
            if list_tag != "ol":
                if list_tag:
                    result.append(f'</{list_tag}>')
                result.append('<ol>')
                list_tag = "ol"
            content = re.sub(r'^\d+\.\s', '', stripped)
            result.append(f'<li>{content}</li>')

        # Empty line
        elif not stripped:
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append('')

        # Preserved HTML blocks (fenced code converted to <pre>)
        elif _BLOCK_TOKEN.fullmatch(stripped):
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append(stripped)

        # Regular paragraph
        else:
            if list_tag:
                result.append(f'</{list_tag}>')
                list_tag = ""
            result.append(f'<p>{stripped}</p>')

    if list_tag:
        result.append(f'</{list_tag}>')

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
