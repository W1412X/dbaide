from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QTextBrowser

from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.theme import Theme
from dbaide.rendering.markdown import render_markdown_safe
from dbaide.rendering.sanitize import escape_user_text


class MarkdownView(QTextBrowser):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFont(QFont("Inter", 12))
        configure_readonly_text_view(self)
        self.setStyleSheet(f"QTextBrowser {{ background: {Theme.BG}; border: none; }}")
        # Style markdown tables/code (insertHtml doesn't honour an inline <style>, so
        # set the document's default stylesheet) — otherwise tables render as plain,
        # borderless runs of text.
        self.document().setDefaultStyleSheet(
            f"table {{ border-collapse: collapse; margin: 8px 0; }}"
            f"th, td {{ border: 1px solid {Theme.BORDER_SOFT}; padding: 5px 10px; }}"
            f"th {{ background: {Theme.PANEL_2}; color: {Theme.TEXT_2}; }}"
            f"pre, code {{ background: {Theme.CODE_BG}; font-family: Menlo, monospace; }}"
            f"a {{ color: {Theme.BLUE}; }}"
        )

    def append_card(
        self,
        title: str,
        markdown: str = "",
        *,
        role: str = "agent",
        badges: list[str] | None = None,
    ) -> None:
        color = Theme.GREEN if role == "user" else Theme.BLUE
        badge_html = ""
        if badges:
            chips = " ".join(
                f"<span style='display:inline-block;margin-right:6px;padding:2px 8px;"
                f"border:1px solid {Theme.BORDER};border-radius:999px;font-size:10px;"
                f"color:{Theme.TEXT_2};'>{escape_user_text(b)}</span>"
                for b in badges
            )
            badge_html = f"<div style='margin-bottom:8px'>{chips}</div>"
        body = render_markdown_safe(markdown or "")
        html = (
            f"<section style='margin:16px 0;padding:14px 16px;border:1px solid {Theme.BORDER_SOFT};"
            f"border-radius:12px;background:{Theme.SURFACE};'>"
            f"<div style='color:{color};font-weight:800;font-size:13px;margin-bottom:8px'>"
            f"{escape_user_text(title)}</div>{badge_html}"
            f"<div style='line-height:1.55;color:{Theme.TEXT}'>{body}</div></section>"
        )
        self._append_html(html)

    def append_sql_block(self, sql: str, *, validation: str = "") -> None:
        if not sql:
            return
        status = f"<span style='color:{Theme.GREEN};font-size:11px;margin-left:8px'>{escape_user_text(validation)}</span>" if validation else ""
        html = (
            f"<div style='margin:10px 0 6px;color:{Theme.TEXT_2};font-weight:700'>SQL{status}</div>"
            f"<pre style='background:{Theme.CODE_BG};color:{Theme.BLUE};border:1px solid {Theme.BORDER};"
            f"border-radius:10px;padding:12px;white-space:pre-wrap;font-family:Menlo,monospace;font-size:11px;'>"
            f"{escape_user_text(sql)}</pre>"
        )
        self._append_html(html)

    def _append_html(self, html: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html)
        cursor.insertBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def clear_view(self) -> None:
        self.clear()
