"""A shared "busy" spinner — the DBAide spiral mark rotating smoothly.

The three concentric arcs from the project logo spin as a single unit, giving
a distinctive "vortex in motion" feel that replaces the generic Lucide loader
arc.  Rendered as a vector (via QSvgRenderer) at the screen's device-pixel-
ratio, so it stays sharp on HiDPI.  Driven off a single QTimer; rotating a few
degrees per tick produces a smooth revolution.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QByteArray, QObject, QRectF, QTimer, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from dbaide.desktop.theme import Theme

# Degrees advanced per tick — 30 deg * ~70 ms  =>  smooth ~0.85 s per revolution.
_ANGLE_STEP = 30
# Logical px — always pair with ``widget.setIconSize(QSize(SPINNER_SIZE, SPINNER_SIZE))``.
SPINNER_SIZE = 15

# ── The project spiral mark as inline SVG ──────────────────────────────────
# Copied from packaging/icons/dbaide.svg but with a configurable stroke colour
# (the gradient is replaced by a solid colour so it tints nicely to match each
# call-site's theme).  The viewBox is centred on the mark so it renders at any
# size without clipping.

_SPINNER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" '
    'viewBox="0 0 1024 1024">'
    '<g fill="none" stroke="{color}" stroke-linecap="round">'
    '<path d="M 512 192 A 320 320 0 1 1 192 512" stroke-width="58"/>'
    '<path d="M 512 304 A 208 208 0 1 0 720 512" stroke-width="50" opacity="0.75"/>'
    '<path d="M 512 392 A 120 120 0 1 1 392 512" stroke-width="42" opacity="0.5"/>'
    '</g></svg>'
)


def _dpr() -> float:
    app = QGuiApplication.instance()
    try:
        if app is not None and app.primaryScreen() is not None:
            return max(2.0, float(app.primaryScreen().devicePixelRatio()))
    except Exception:  # noqa: BLE001
        pass
    return 2.0


def spinner_pixmap(angle: float, *, size: int = SPINNER_SIZE, color: str = Theme.BLUE, **_kw) -> QPixmap:
    """The DBAide spiral mark rotated to *angle* — crisp vector, HiDPI-aware.

    The ``color`` and ``**_kw`` signature is kept compatible with the old Lucide
    loader API so callers don't need changes (``width`` is silently ignored).
    """
    dpr = _dpr()
    px_size = int(round(size * dpr))
    px = QPixmap(px_size, px_size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(dpr, dpr)
    if angle:
        painter.translate(size / 2, size / 2)
        painter.rotate(angle)
        painter.translate(-size / 2, -size / 2)
    svg_data = _SPINNER_SVG.format(color=color)
    renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def spinner_icon(angle: float, *, size: int = SPINNER_SIZE, color: str = Theme.BLUE, **_kw) -> QIcon:
    return QIcon(spinner_pixmap(angle, size=size, color=color))


class BusyAnimator(QObject):
    """Calls ``on_tick()`` on a timer while started, advancing ``angle`` each time."""

    def __init__(self, on_tick: Callable[[], None], *, interval_ms: int = 70, parent=None) -> None:
        super().__init__(parent)
        self._on_tick = on_tick
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def angle(self) -> float:
        return self._angle

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if not self._timer.isActive():
            self._angle = 0.0
            self._on_tick()
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle + _ANGLE_STEP) % 360
        self._on_tick()
