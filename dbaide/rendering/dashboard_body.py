"""Render an AI dashboard from a declarative COMPONENT TREE — never free HTML.

The builder agent emits a flexible, nestable tree (a kind of structured pseudocode)
plus the recipes; the SYSTEM renders it. Two component families:

  containers (nest children): page · row · col/stack · grid · section · card · tabs/tab
  leaves (content):           chart · kpi · table · text/markdown · heading · divider

Flexibility comes from composition (nest freely) and an extensible component set —
adding a component is one deterministic renderer here, not model-authored markup.
Robustness: an unknown container renders its children; an unknown leaf is skipped; a
tile pointing at a missing recipe is dropped; if nothing renders we fall back to an
auto-grid of every recipe. A control bar is auto-generated from the recipe params.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Iterable

from dbaide.i18n import t as _t

_LEAVES = {"chart", "kpi", "table", "text", "markdown", "heading", "divider"}
_STACKERS = {"page", "col", "stack", "section", "card", "group", "tab", "root"}


# -- controls (auto-generated from recipe params) ---------------------------

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
        raw_sel = (default if isinstance(default, (list, tuple))
                   else ([default] if default not in (None, "") else []))
        selected = {str(x) for x in raw_sel}   # str-normalize so a numeric default matches a string option
        if getattr(p, "multi", False):
            checks = "".join(
                f'<label class="dbaide-check"><input type="checkbox" data-param="{name}" '
                f'value="{escape(str(o))}"{" checked" if str(o) in selected else ""}>{escape(str(o))}</label>'
                for o in options)
            bar = (f'<div class="dbaide-ckbar"><button type="button" data-ckall>{escape(_t("dash.select_all"))}</button>'
                   f'<button type="button" data-ckno>{escape(_t("dash.clear"))}</button></div>')
            return (f'<details class="dbaide-dd"><summary data-ddlabel="{label}">{label}</summary>'
                    f'<div class="dbaide-checklist">{bar}{checks}</div></details>')
        opts = "".join(
            f'<option value="{escape(str(o))}"{" selected" if str(o) in selected else ""}>{escape(str(o))}</option>'
            for o in options)
        return f'<label>{label}<select data-param="{name}">{opts}</select></label>'
    html_type = {"date": "date", "number": "number"}.get(ptype, "text")
    # resolve a dynamic @token default (e.g. @month_start) to a concrete value so the
    # initial filter condition is VISIBLE and editable in the control, not a blank box
    if isinstance(default, str) and default.startswith("@"):
        try:
            from dbaide.boards.dates import resolve_value
            default = resolve_value(default)
        except Exception:  # noqa: BLE001
            default = ""
    val = "" if default in (None, "") or isinstance(default, (list, tuple)) else escape(str(default))
    val_attr = f' value="{val}"' if val else ""
    return f'<label>{label}<input type="{html_type}" data-param="{name}"{val_attr}></label>'


def render_controls(charts: list[Any]) -> str:
    params = _dedup_params(charts)
    if not params:
        return ""
    controls = "".join(_control(p) for p in params)
    return f'<div class="dbaide-controls">{controls}<button data-apply>{escape(_t("dash.apply"))}</button></div>'


# -- helpers ----------------------------------------------------------------

def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


_INLINE_MD = (
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"`(.+?)`"), r"<code>\1</code>"),
)


def _inline_md(raw: str) -> str:
    s = escape(raw)
    for pat, repl in _INLINE_MD:
        s = pat.sub(repl, s)
    return s


def _is_md_table_sep(line: str) -> bool:
    """A GFM table separator row, e.g. ``|---|:--:|``."""
    s = line.strip()
    if "|" not in s:
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(c and set(c) <= set("-: ") and "-" in c for c in cells)


def _md_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _mini_markdown(text: str) -> str:
    """Tiny, safe markdown for text/markdown leaves: headings, bold/code, quotes, lists,
    line breaks, AND GFM pipe tables (so a composed summary table renders as a table)."""
    lines = str(text or "").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        # a pipe table: a row with '|' followed by a separator row
        if "|" in raw and i + 1 < n and _is_md_table_sep(lines[i + 1]):
            header = [_inline_md(c) for c in _md_cells(raw)]
            i += 2
            body: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body.append([_inline_md(c) for c in _md_cells(lines[i])])
                i += 1
            ncol = len(header)
            th = "".join(f"<th>{c}</th>" for c in header)
            trs = "".join("<tr>" + "".join(f"<td>{row[j] if j < len(row) else ''}</td>"
                                           for j in range(ncol)) + "</tr>" for row in body)   # pad ragged rows
            out.append(f'<table class="dbaide-md-table"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>')
            continue
        # a list block: group consecutive bullet lines into one <ul>
        if raw.strip().startswith("- ") or raw.strip().startswith("* "):
            items = []
            while i < n and (lines[i].strip().startswith("- ") or lines[i].strip().startswith("* ")):
                items.append(f"<li>{_inline_md(lines[i].strip()[2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        line = _inline_md(raw)
        s = line.strip()
        if s.startswith("&gt; "):
            out.append(f'<blockquote>{s[5:]}</blockquote>')
        elif s.startswith("### "):
            out.append(f'<h4>{s[4:]}</h4>')
        elif s.startswith("## "):
            out.append(f'<h3>{s[3:]}</h3>')
        elif s.startswith("# "):
            out.append(f'<h2>{s[2:]}</h2>')
        elif s == "":
            out.append("<br>")
        else:
            out.append(line + "<br>")
        i += 1
    return "".join(out)


def _chart_id(node: dict[str, Any]) -> str:
    return str(node.get("chart") or node.get("chart_id") or "")


# -- sizing: the system decides appropriate sizes by chart type (the AI's guesses are
#    unreliable). (default_height, min, max) per type; defaults cover bar/line/area/etc. --
_CHART_H = {
    "pie": (300, 240, 440), "donut": (300, 240, 440), "radar": (320, 260, 460),
    "gauge": (260, 200, 360), "funnel": (300, 240, 460),
    "scatter": (320, 260, 500), "bubble": (320, 260, 500), "boxplot": (320, 260, 520),
    "candlestick": (340, 260, 560),
    "heatmap": (380, 300, 640), "treemap": (380, 300, 640), "sunburst": (380, 300, 640),
    "sankey": (400, 320, 640), "tree": (380, 300, 640),
}
_CHART_H_DEFAULT = (300, 220, 540)
_NARROW_TYPES = {"pie", "donut", "radar", "gauge", "funnel"}
_WIDE_TYPES = {"heatmap", "treemap", "sunburst", "sankey", "tree"}
_SIZE_SPAN = {"sm": 3, "md": 6, "lg": 8, "wide": 12, "full": 12}


def _chart_height(ctype: str, requested: Any) -> int:
    """Type-aware height: use the type default when unset; clamp the AI's value into a
    sane band for that type (so a pie can't be 600px tall, a bar can't be 120px)."""
    d, lo, hi = _CHART_H.get(ctype, _CHART_H_DEFAULT)
    if requested is None:
        return d
    try:
        return max(lo, min(hi, int(requested)))
    except (TypeError, ValueError):
        return d


def _natural_span(node: dict[str, Any], ctx: dict[str, Any]) -> int:
    kind = str(node.get("type") or "").lower()
    if kind == "kpi":
        return 3
    if kind in ("text", "markdown", "heading", "divider"):
        return 12
    if kind == "table":
        return 8
    ctype = ctx.get("types", {}).get(_chart_id(node), "")
    if ctype in _NARROW_TYPES:
        return 4
    if ctype in _WIDE_TYPES:
        return 8
    return 6


def _tile_span(node: dict[str, Any], ctx: dict[str, Any]) -> int:
    """Explicit span wins; else a "size" class (sm/md/lg/wide/full); else a natural span
    derived from the tile/chart type — so the AI can omit sizing and still look right."""
    if node.get("span") is not None:
        return _clamp(node.get("span"), 1, 12, 6)
    sz = str(node.get("size") or "").lower()
    if sz in _SIZE_SPAN:
        return _SIZE_SPAN[sz]
    return _natural_span(node, ctx)


# -- the recursive renderer -------------------------------------------------

def _render_leaf(node: dict[str, Any], ntype: str, ctx: dict[str, Any]) -> str:
    if ntype == "divider":
        return '<hr class="dbaide-divider">'
    if ntype in ("text", "markdown"):
        body = _mini_markdown(node.get("text") or node.get("content") or "")
        return f'<div class="dbaide-text">{body}</div>'
    if ntype == "heading":
        return f'<div class="dbaide-heading">{escape(str(node.get("text") or node.get("title") or ""))}</div>'

    cid = _chart_id(node)
    if not cid or cid not in ctx["valid"]:
        return ""   # references a recipe that doesn't exist → drop
    ctx["covered"].add(cid)
    ecid = escape(cid)
    title = escape(str(node.get("title") or ""))
    title_html = f'<div class="dbaide-card-title">{title}</div>' if title else ""
    if ntype == "kpi":
        label = escape(str(node.get("label") or node.get("title") or ""))
        fmt = escape(str(node.get("format") or ""))
        trend = "1" if node.get("trend") else ""
        return (f'<div class="dbaide-card dbaide-kpi" data-chart="{ecid}" data-kind="kpi"'
                f' data-format="{fmt}" data-trend="{trend}">'
                f'<div class="dbaide-kpi-label">{label}</div>'
                f'<div class="dbaide-kpi-value">…</div>'
                f'<div class="dbaide-kpi-spark"></div></div>')
    if ntype == "table":
        return (f'<div class="dbaide-card">{title_html}'
                f'<div data-chart="{ecid}" data-kind="table" class="dbaide-table-wrap"></div></div>')
    ctype = ctx.get("types", {}).get(cid, "")   # chart: type-aware, clamped height
    h = _chart_height(ctype, node.get("height"))
    return (f'<div class="dbaide-card">{title_html}'
            f'<div data-chart="{ecid}" data-kind="chart" data-ctype="{escape(ctype)}" '
            f'style="height:{h}px"></div></div>')


def _render_children(children: Any, ctx: dict[str, Any]) -> str:
    return "".join(render_node(c, ctx) for c in (children or []) if isinstance(c, dict))


def _render_row(children: Any, ctx: dict[str, Any]) -> str:
    kids = [c for c in (children or []) if isinstance(c, dict)]
    n = max(1, len(kids))
    cells = []
    for c in kids:
        inner = render_node(c, ctx)
        if not inner:
            continue
        span = _tile_span(c, ctx)   # explicit span › size class › natural-by-type
        cells.append(f'<div class="dbaide-cell" style="grid-column:span {span}">{inner}</div>')
    return f'<div class="dbaide-row">{"".join(cells)}</div>' if cells else ""


def _render_tabs(children: Any, ctx: dict[str, Any]) -> str:
    tabs = [c for c in (children or []) if isinstance(c, dict) and str(c.get("type", "")).lower() == "tab"]
    if not tabs:
        return _render_children(children, ctx)   # not real tabs → just stack
    uid = ctx["uid"][0]
    ctx["uid"][0] += 1
    heads, panels = [], []
    for i, t in enumerate(tabs):
        label = escape(str(t.get("label") or t.get("title") or f"Tab {i + 1}"))
        on = " active" if i == 0 else ""
        key = f"{uid}-{i}"
        heads.append(f'<button class="dbaide-tab{on}" data-tab="{key}">{label}</button>')
        panels.append(f'<div class="dbaide-tabpanel{on}" data-tabpanel="{key}">'
                      f'{_render_children(t.get("children"), ctx)}</div>')
    return (f'<div class="dbaide-tabs"><div class="dbaide-tabbar">{"".join(heads)}</div>'
            f'{"".join(panels)}</div>')


def render_node(node: Any, ctx: dict[str, Any]) -> str:
    if not isinstance(node, dict):
        return ""
    ntype = str(node.get("type") or "").lower()
    children = node.get("children")
    if ntype in _LEAVES:
        return _render_leaf(node, ntype, ctx)
    if ntype == "row":
        return _render_row(children, ctx)
    if ntype == "grid":
        cols = _clamp(node.get("cols"), 1, 6, 3)
        cells = "".join(f'<div>{render_node(c, ctx)}</div>'
                        for c in (children or []) if isinstance(c, dict))
        return f'<div class="dbaide-grid2" style="grid-template-columns:repeat({cols},1fr)">{cells}</div>'
    if ntype == "section":
        title = escape(str(node.get("title") or ""))
        head = f'<div class="dbaide-heading">{title}</div>' if title else ""
        return f'<div class="dbaide-section">{head}{_render_children(children, ctx)}</div>'
    if ntype in ("card", "group"):
        return f'<div class="dbaide-card">{_render_children(children, ctx)}</div>'
    if ntype == "tabs":
        return _render_tabs(children, ctx)
    if ntype in _STACKERS:
        return _render_children(children, ctx)
    # unknown type: passthrough children if any, else skip
    return _render_children(children, ctx) if children else ""


# -- entry points -----------------------------------------------------------

def _chart_card(c: Any) -> str:
    return (f'<div class="dbaide-card">'
            f'<div class="dbaide-card-title">{escape(str(getattr(c, "title", "") or getattr(c, "chart_id", "")))}</div>'
            f'<div data-chart="{escape(str(getattr(c, "chart_id", "")))}" data-kind="chart" style="height:280px"></div>'
            f'</div>')


def auto_grid(charts: list[Any]) -> str:
    """The safe fallback: one chart card per recipe in a responsive grid."""
    cards = "".join(_chart_card(c) for c in charts if getattr(c, "chart_id", ""))
    return render_controls(charts) + f'<div class="dbaide-grid">{cards}</div>'


def _looks_like_legacy_rows(layout: Any) -> bool:
    return (isinstance(layout, list) and bool(layout)
            and isinstance(layout[0], dict) and "tiles" in layout[0])


def _render_legacy_rows(layout: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    """Back-compat for dashboards saved under the old rows/tiles schema (tiles use
    'kind'; the tree uses 'type')."""
    out = []
    for row in layout:
        if not isinstance(row, dict):
            continue
        tiles = [{**t, "type": t.get("kind", "chart")}
                 for t in (row.get("tiles") or []) if isinstance(t, dict)]
        out.append(_render_row(tiles, ctx))
    return "".join(out)


def render_body(layout: Any, charts: list[Any]) -> str:
    """Render the declarative *layout* (component tree, or legacy rows) into a themed,
    system-owned body; always safe; falls back to an auto-grid when unusable."""
    charts = charts or []  # honour the "always safe" contract — a board may have no charts
    valid = {str(getattr(c, "chart_id", "") or "") for c in charts if getattr(c, "chart_id", "")}
    types = {str(getattr(c, "chart_id", "") or ""):
             str((getattr(c, "chart_plan", None) or {}).get("chart_type") or "").lower()
             for c in charts if getattr(c, "chart_id", "")}
    ctx = {"valid": valid, "types": types, "covered": set(), "uid": [0]}

    if isinstance(layout, dict):
        body = render_node(layout, ctx)
    elif _looks_like_legacy_rows(layout):
        body = _render_legacy_rows(layout, ctx)
    elif isinstance(layout, list):
        body = _render_children(layout, ctx)
    else:
        body = ""

    if not body.strip():
        return auto_grid(charts)
    # the tree stands; append any recipe it forgot to place so nothing is lost
    uncovered = [c for c in charts if getattr(c, "chart_id", "") and str(c.chart_id) not in ctx["covered"]]
    tail = ("<div class=\"dbaide-grid\">" + "".join(_chart_card(c) for c in uncovered) + "</div>") if uncovered else ""
    return render_controls(charts) + body + tail
