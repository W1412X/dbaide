"""Make an AI-authored dashboard body safe and always renderable.

The builder agent writes a free-form HTML body. That output is not trustworthy:
it may inject <script>, omit a chart container, reference a chart_id that has no
recipe, or forget the apply button (so filters never take effect). This module
normalizes it deterministically — and when the body is missing or doesn't cover
every recipe, falls back to a clean, theme-consistent layout generated from the
recipes themselves. The result: generation quality never breaks the page.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Iterable

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_CHART_RE = re.compile(r'data-chart\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def strip_scripts(html: str) -> str:
    """Remove any <script> the model wrongly included (the host owns all JS)."""
    return _SCRIPT_RE.sub("", html or "")


def chart_container_ids(html: str) -> set[str]:
    return {m.group(1) for m in _CHART_RE.finditer(html or "")}


def _control(p: Any) -> str:
    name = escape(str(getattr(p, "name", "") or ""))
    label = escape(str(getattr(p, "label", "") or getattr(p, "name", "") or ""))
    ptype = str(getattr(p, "type", "text") or "text")
    options = list(getattr(p, "options", None) or [])
    if ptype == "enum" and options:
        default = getattr(p, "default", None)
        selected = (set(default) if isinstance(default, (list, tuple))
                    else ({default} if default not in (None, "") else set()))
        multi = " multiple size=\"3\"" if getattr(p, "multi", False) else ""
        opts = "".join(
            f'<option value="{escape(str(o))}"{" selected" if o in selected else ""}>{escape(str(o))}</option>'
            for o in options)
        field = f'<select data-param="{name}"{multi}>{opts}</select>'
    else:
        html_type = {"date": "date", "number": "number"}.get(ptype, "text")
        default = getattr(p, "default", None)
        # @tokens (e.g. @month_start) aren't valid control values; leave the field empty
        # and let the runtime resolve the default when the value comes back blank
        val = "" if (isinstance(default, str) and default.startswith("@")) else (
            "" if default in (None, "") or isinstance(default, (list, tuple)) else escape(str(default)))
        val_attr = f' value="{val}"' if val else ""
        field = f'<input type="{html_type}" data-param="{name}"{val_attr}>'
    return f'<label>{label}{field}</label>'


def _dedup_params(charts: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for c in charts:
        for p in getattr(c, "params", None) or []:
            nm = str(getattr(p, "name", "") or "")
            if nm and nm not in seen:
                seen.add(nm)
                out.append(p)
    return out


def default_body(charts: list[Any]) -> str:
    """A clean, theme-styled layout built straight from the recipes."""
    params = _dedup_params(charts)
    bar = ""
    if params:
        controls = "".join(_control(p) for p in params)
        bar = (f'<div class="dbaide-controls">{controls}'
               f'<button data-apply>应用</button></div>')
    cards = "".join(
        f'<div class="dbaide-card">'
        f'<div class="dbaide-card-title">{escape(str(getattr(c, "title", "") or getattr(c, "chart_id", "")))}</div>'
        f'<div data-chart="{escape(str(getattr(c, "chart_id", "")))}" style="height:280px"></div>'
        f'</div>'
        for c in charts if getattr(c, "chart_id", "")
    )
    return f'{bar}<div class="dbaide-grid">{cards}</div>'


def normalize_body(html: str, charts: list[Any]) -> str:
    """Return a body that is guaranteed safe and renders every recipe.

    Keeps the model's HTML when it's usable (so chat refinement works), otherwise
    falls back to :func:`default_body`. Always strips scripts and guarantees an
    apply trigger when there are filters.
    """
    needed = {str(getattr(c, "chart_id", "") or "") for c in charts if getattr(c, "chart_id", "")}
    body = strip_scripts(html or "")
    present = chart_container_ids(body)
    # the model body is only trustworthy if it has a container for EVERY recipe chart
    if not body.strip() or not needed or not needed.issubset(present):
        return default_body(charts)
    has_params = any(getattr(c, "params", None) for c in charts)
    if has_params and "data-apply" not in body:
        # filters can't be applied without a trigger — prepend a minimal one
        body = '<div class="dbaide-controls"><button data-apply>应用</button></div>' + body
    return body
