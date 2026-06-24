"""Render an AI dashboard from a declarative layout spec — never from free HTML.

The builder agent emits a structured spec (rows of tiles) plus the recipes; the
SYSTEM owns the rendering. This module turns that spec into a themed 12-column
grid. Tiles reference a recipe ``chart`` and have a ``kind``:

    chart   — an ECharts chart (the default)
    kpi     — a single big metric value + label
    table   — the recipe's rows as a compact table
    heading — a static section title (no data; uses ``text``)

A control bar is auto-generated from the de-duplicated recipe params, so the
filters always match the queries. Anything malformed (missing/garbled spec, a
tile pointing at an unknown chart) falls back to a clean auto-grid of every
recipe — generation quality can never break or empty the page.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable

_KINDS = {"chart", "kpi", "table", "heading"}


# -- controls ---------------------------------------------------------------

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


def _control(p: Any) -> str:
    name = escape(str(getattr(p, "name", "") or ""))
    label = escape(str(getattr(p, "label", "") or getattr(p, "name", "") or ""))
    ptype = str(getattr(p, "type", "text") or "text")
    options = list(getattr(p, "options", None) or [])
    default = getattr(p, "default", None)
    if ptype == "enum" and options:
        selected = (set(default) if isinstance(default, (list, tuple))
                    else ({default} if default not in (None, "") else set()))
        multi = " multiple size=\"3\"" if getattr(p, "multi", False) else ""
        opts = "".join(
            f'<option value="{escape(str(o))}"{" selected" if o in selected else ""}>{escape(str(o))}</option>'
            for o in options)
        field = f'<select data-param="{name}"{multi}>{opts}</select>'
    else:
        html_type = {"date": "date", "number": "number"}.get(ptype, "text")
        # @tokens (e.g. @month_start) aren't valid control values; leave the field empty
        # and let the runtime resolve the default when the value comes back blank
        val = "" if (isinstance(default, str) and default.startswith("@")) else (
            "" if default in (None, "") or isinstance(default, (list, tuple)) else escape(str(default)))
        val_attr = f' value="{val}"' if val else ""
        field = f'<input type="{html_type}" data-param="{name}"{val_attr}>'
    return f'<label>{label}{field}</label>'


def render_controls(charts: list[Any]) -> str:
    params = _dedup_params(charts)
    if not params:
        return ""
    controls = "".join(_control(p) for p in params)
    return f'<div class="dbaide-controls">{controls}<button data-apply>应用</button></div>'


# -- tiles ------------------------------------------------------------------

def _clamp_span(span: Any) -> int:
    try:
        return max(1, min(12, int(span)))
    except (TypeError, ValueError):
        return 12


def _tile_html(tile: dict[str, Any], valid_ids: set[str]) -> str | None:
    kind = str(tile.get("kind") or "chart").lower()
    if kind not in _KINDS:
        kind = "chart"
    span = _clamp_span(tile.get("span", 12))
    style = f"grid-column:span {span};"

    if kind == "heading":
        text = escape(str(tile.get("text") or tile.get("title") or ""))
        return f'<div class="dbaide-heading" style="{style}">{text}</div>'

    chart_id = str(tile.get("chart") or tile.get("chart_id") or "")
    if not chart_id or chart_id not in valid_ids:
        return None   # tile points at a recipe that doesn't exist — drop it
    cid = escape(chart_id)
    title = escape(str(tile.get("title") or ""))
    title_html = f'<div class="dbaide-card-title">{title}</div>' if title else ""

    if kind == "kpi":
        label = escape(str(tile.get("label") or tile.get("title") or ""))
        return (f'<div class="dbaide-card dbaide-kpi" style="{style}">'
                f'<div class="dbaide-kpi-value" data-chart="{cid}" data-kind="kpi">…</div>'
                f'<div class="dbaide-kpi-label">{label}</div></div>')
    if kind == "table":
        return (f'<div class="dbaide-card" style="{style}">{title_html}'
                f'<div data-chart="{cid}" data-kind="table" class="dbaide-table-wrap"></div></div>')
    # chart
    height = tile.get("height")
    h = 280
    try:
        h = max(140, min(640, int(height)))
    except (TypeError, ValueError):
        pass
    return (f'<div class="dbaide-card" style="{style}">{title_html}'
            f'<div data-chart="{cid}" data-kind="chart" style="height:{h}px"></div></div>')


def _rows(spec: Any) -> list[dict[str, Any]]:
    """Accept either a bare list of rows or {"rows":[...]}."""
    if isinstance(spec, dict):
        spec = spec.get("rows")
    return [r for r in (spec or []) if isinstance(r, dict)]


# -- entry points -----------------------------------------------------------

def auto_grid(charts: list[Any]) -> str:
    """The safe fallback: one chart card per recipe in a responsive grid."""
    cards = "".join(
        f'<div class="dbaide-card">'
        f'<div class="dbaide-card-title">{escape(str(getattr(c, "title", "") or getattr(c, "chart_id", "")))}</div>'
        f'<div data-chart="{escape(str(getattr(c, "chart_id", "")))}" data-kind="chart" style="height:280px"></div>'
        f'</div>'
        for c in charts if getattr(c, "chart_id", "")
    )
    return render_controls(charts) + f'<div class="dbaide-grid">{cards}</div>'


def render_body(layout: Any, charts: list[Any]) -> str:
    """Render the declarative *layout* into a themed body; fall back to auto-grid.

    The body is fully system-owned HTML (no model markup), so it is safe and
    consistent by construction.
    """
    valid_ids = {str(getattr(c, "chart_id", "") or "") for c in charts if getattr(c, "chart_id", "")}
    rows = _rows(layout)
    rendered_rows: list[str] = []
    covered: set[str] = set()
    for row in rows:
        tiles = [t for t in (row.get("tiles") or []) if isinstance(t, dict)]
        cells = []
        for t in tiles:
            html = _tile_html(t, valid_ids)
            if html:
                cells.append(html)
                cid = str(t.get("chart") or t.get("chart_id") or "")
                if cid:
                    covered.add(cid)
        if cells:
            rendered_rows.append(f'<div class="dbaide-row">{"".join(cells)}</div>')

    # the spec must be present AND cover every recipe, else it isn't trustworthy
    if not rendered_rows or not valid_ids.issubset(covered):
        return auto_grid(charts)
    return render_controls(charts) + "".join(rendered_rows)
