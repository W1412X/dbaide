"""Sanitization utilities for safe text rendering."""
from __future__ import annotations

import html
import re


# Sensitive patterns to redact
_SENSITIVE_PATTERNS = [
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), '<EMAIL>'),
    (re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'), '<PHONE>'),
    (re.compile(r'\b\d{15,19}\b'), '<CARD>'),
    (re.compile(r'\b(?:sk-|api[_-]?key[_-]?=|token[_-]?=|password[_-]?=)\s*\S+', re.I), '<SECRET>'),
]

# SQL keywords for highlighting
_SQL_KEYWORDS = {
    'select', 'from', 'where', 'and', 'or', 'not', 'in', 'between', 'like',
    'join', 'inner', 'left', 'right', 'outer', 'cross', 'on', 'as',
    'group', 'by', 'order', 'asc', 'desc', 'limit', 'offset', 'fetch',
    'having', 'union', 'all', 'distinct', 'insert', 'into', 'values',
    'update', 'set', 'delete', 'create', 'table', 'alter', 'drop',
    'index', 'view', 'trigger', 'procedure', 'function', 'case', 'when',
    'then', 'else', 'end', 'null', 'is', 'exists', 'any', 'some',
    'count', 'sum', 'avg', 'min', 'max', 'coalesce', 'nullif', 'cast',
    'with', 'recursive', 'lateral', 'unnest', 'array', 'json',
}


def escape_user_text(text: str) -> str:
    """Escape user text for safe HTML display.

    Converts special characters to HTML entities to prevent XSS.
    """
    return html.escape(str(text), quote=True)


def sanitize_markdown_html(html_text: str) -> str:
    """Sanitize HTML rendered from Markdown.

    Removes dangerous tags and attributes while keeping safe formatting.
    """
    # Remove script tags and content
    html_text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.S | re.I)
    # Remove event handlers
    html_text = re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', html_text, flags=re.I)
    # Remove javascript: URLs
    html_text = re.sub(r'javascript\s*:', '', html_text, flags=re.I)
    # Remove data: URLs (except images)
    html_text = re.sub(r'data\s*:(?!image)', '', html_text, flags=re.I)
    return html_text


def redact_sensitive_text(text: str) -> str:
    """Redact sensitive information from text.

    Replaces emails, phone numbers, API keys, passwords with placeholders.
    """
    result = str(text)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def highlight_sql(sql: str) -> str:
    """Add basic SQL syntax highlighting with HTML spans.

    Returns HTML with keywords in blue, strings in green, numbers in orange.
    """
    escaped = escape_user_text(sql)

    # Highlight strings
    escaped = re.sub(
        r&#39;(?:[^&#39;\\]|\\.)*&#39;',
        r'<span style="color:#22863a;">\g<0></span>',
        escaped,
    )
    escaped = re.sub(
        r'&quot;(?:[^&quot;\\]|\\.)*&quot;',
        r'<span style="color:#22863a;">\g<0></span>',
        escaped,
    )

    # Highlight numbers
    escaped = re.sub(
        r'\b(\d+\.?\d*)\b',
        r'<span style="color:#e36209;">\1</span>',
        escaped,
    )

    # Highlight keywords
    for kw in _SQL_KEYWORDS:
        escaped = re.sub(
            rf'\b({kw})\b',
            r'<span style="color:#0550ae;font-weight:600;">\1</span>',
            escaped,
            flags=re.I,
        )

    return escaped
