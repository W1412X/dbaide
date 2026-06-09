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
    # Remove event handlers (quoted and unquoted attribute values)
    html_text = re.sub(r'\bon\w+\s*=\s*(?:["\'][^"\']*["\']|\S+)', '', html_text, flags=re.I)
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
