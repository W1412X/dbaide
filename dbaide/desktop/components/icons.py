"""Crisp vector icons rendered from Lucide (https://lucide.dev, ISC-licensed) SVG
path data — not hand-drawn QPainter shapes (those blurred at small sizes).

Each icon is a 24×24 stroke glyph; we render it to a QPixmap at the screen's
device-pixel-ratio so it stays sharp on HiDPI, tinted to the requested colour.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from dbaide.desktop.theme import Theme

# Inner SVG of each Lucide glyph (stroke inherits from the wrapper's `stroke`).
_GLYPHS: dict[str, str] = {
    "panel-right": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
    "more-horizontal": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "link": ('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
             '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'),
    "settings": ('<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73'
                 'l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38'
                 'a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18'
                 'a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08'
                 'a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08'
                 'a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'),
    "copy": ('<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
             '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'),
    "trash": ('<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
              '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/>'
              '<line x1="14" x2="14" y1="11" y2="17"/>'),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "send": '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "refresh": ('<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
                '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/>'),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "pencil": ('<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352'
               'a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>'),
    "database": ('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/>'
                 '<path d="M3 12A9 3 0 0 0 21 12"/>'),
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "external-link": ('<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
                      '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',  # 270° arc — the spinner
    "table": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 3v18"/>',
    "columns": '<path d="M12 3v18"/><path d="M3 12h18"/>',
    # Format / beautify SQL.
    "sparkles": ('<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 '
                 '9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 '
                 '0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z"/>'
                 '<path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>'),
    # Execution plan (a small query tree).
    "list-tree": ('<path d="M21 12h-8"/><path d="M21 6H8"/><path d="M21 18h-8"/>'
                  '<path d="M3 6v4c0 1.1.9 2 2 2h3"/><path d="M3 10v6c0 1.1.9 2 2 2h3"/>'),
    # Document / offline doc (file with text lines).
    "file-text": ('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
                  '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/>'
                  '<path d="M10 9H8"/>'),
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round" '
    'stroke-linejoin="round">{inner}</svg>'
)


def _dpr() -> float:
    app = QGuiApplication.instance()
    try:
        if app is not None and app.primaryScreen() is not None:
            return max(2.0, float(app.primaryScreen().devicePixelRatio()))
    except Exception:  # noqa: BLE001
        pass
    return 2.0  # render at ≥2× so it's crisp even off a real screen


def _renderer(name: str, color: str, width: float) -> QSvgRenderer:
    svg = _TEMPLATE.format(color=color, w=width, inner=_GLYPHS[name])
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def svg_pixmap(name: str, *, color: str = Theme.MUTED, size: int = 18, width: float = 2.0,
               angle: float = 0.0) -> QPixmap:
    dpr = _dpr()
    px = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(dpr, dpr)
    if angle:
        painter.translate(size / 2, size / 2)
        painter.rotate(angle)
        painter.translate(-size / 2, -size / 2)
    _renderer(name, color, width).render(painter, QRectF(0, 0, size, size))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def svg_icon(name: str, *, color: str = Theme.MUTED, size: int = 18, width: float = 2.0) -> QIcon:
    return QIcon(svg_pixmap(name, color=color, size=size, width=width))


# ── named helpers (back-compatible signatures) ─────────────────────────────--

def panel_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    return svg_icon("panel-right", color=color, size=size)


def more_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    return svg_icon("more-horizontal", color=color, size=size, width=2.4)


def plus_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    return svg_icon("plus", color=color, size=size)


def clock_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    return svg_icon("clock", color=color, size=size)


def link_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    return svg_icon("link", color=color, size=size)


ICON_SIZE = QSize(16, 16)
