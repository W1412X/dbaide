"""Safe Markdown rendering for DBAide.

Rendering is delegated to **mistune** (a small, pure-Python CommonMark + GFM
library) — it handles the cases a hand-rolled regex renderer keeps getting wrong:
inline code containing ``*`` / ``_``, nested emphasis, escaping, tables with
inline markup, etc. mistune is a required dependency of the GUI (the only place
that renders Markdown to HTML), so the primary path always applies.

If mistune ever raises at runtime we fall back to a TRIVIAL always-safe renderer
(escape everything, keep fenced code as ``<pre>``, newlines → ``<br>``) — not a
second Markdown engine. The old ~200-line regex renderer was removed: it was the
source of the very bugs mistune fixes and was effectively never exercised.
"""
from __future__ import annotations

import re

from dbaide.rendering.sanitize import escape_user_text, sanitize_markdown_html

try:  # Preferred path: a real Markdown parser.
    import mistune

    # escape=True → raw inline HTML in the text is escaped (XSS-safe). The `table`
    # plugin gives GitHub pipe tables; `strikethrough` and `url` are cheap niceties.
    _MISTUNE = mistune.create_markdown(
        escape=True,
        plugins=["table", "strikethrough", "url"],
    )
except Exception:  # noqa: BLE001 — any import/init failure → use the trivial fallback
    _MISTUNE = None


def render_markdown_safe(text: str) -> str:
    """Render Markdown to safe HTML.

    - Escapes raw HTML in user text (no XSS)
    - Supports bold, italic, code, fenced code, lists, headers, links, tables
    - Sanitizes output as a defense-in-depth final pass
    """
    if not text:
        return ""
    if _MISTUNE is not None:
        try:
            html = _MISTUNE(str(text))
            # Tag tables so the app's `table.md-table` CSS styles them.
            html = re.sub(r"<table(?![^>]*\bclass=)", '<table class="md-table"', html)
            return sanitize_markdown_html(html)
        except Exception:  # noqa: BLE001 — never let rendering throw; fall back
            pass
    return _safe_fallback(str(text))


def _safe_fallback(text: str) -> str:
    """Always-safe minimal rendering used only if mistune raises: escape everything,
    keep fenced code blocks as ``<pre>``, and turn newlines into ``<br>``."""
    parts = re.split(r"```", text)
    out: list[str] = []
    for index, part in enumerate(parts):
        escaped = escape_user_text(part)
        if index % 2 == 1:  # text between a pair of ``` fences
            out.append(f"<pre>{escaped}</pre>")
        else:
            out.append(escaped.replace("\n", "<br>"))
    return sanitize_markdown_html("".join(out))
