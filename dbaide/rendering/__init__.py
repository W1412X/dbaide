"""Rendering layer for DBAide - safe Markdown, SQL, table rendering."""
from dbaide.rendering.sanitize import escape_user_text, sanitize_markdown_html, redact_sensitive_text
from dbaide.rendering.markdown import render_markdown_safe
from dbaide.rendering.markdown_page import render_markdown_html
from dbaide.rendering.compose import compose_blocks, compose_document
from dbaide.rendering.answer_export import export_answer_html, suggest_export_filename
from dbaide.rendering.answer_page import render_answer_page_html
from dbaide.rendering.answer_render import build_answer_document_html, default_answer_theme
from dbaide.rendering.table import export_csv, export_markdown_table, format_result_text

__all__ = [
    "escape_user_text",
    "sanitize_markdown_html",
    "redact_sensitive_text",
    "render_markdown_safe",
    "render_markdown_html",
    "render_answer_page_html",
    "build_answer_document_html",
    "default_answer_theme",
    "export_answer_html",
    "suggest_export_filename",
    "compose_blocks",
    "compose_document",
    "export_csv",
    "export_markdown_table",
    "format_result_text",
]
