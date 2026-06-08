"""A shared "busy" spinner — the DBAide spiral mark with counter-rotating arcs.

The three concentric arcs from the project logo each spin independently:
outer clockwise, middle counter-clockwise, inner clockwise — creating a
mesmerising "living vortex" effect.  Rendered as a vector (via QSvgRenderer)
at the screen's device-pixel-ratio, so it stays sharp on HiDPI.  Driven off
a single QTimer; rotating a few degrees per tick produces smooth motion.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QByteArray, QObject, QRectF, QTimer, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from dbaide.desktop.theme import Theme

# Degrees advanced per tick — 30 deg * ~70 ms => smooth ~0.85 s per revolution.
_ANGLE_STEP = 30
# Logical px — always pair with ``widget.setIconSize(QSize(SPINNER_SIZE, SPINNER_SIZE))``.
SPINNER_SIZE = 15

# ── Per-arc rotation multipliers ───────────────────────────────────────────
# Adjacent arcs spin in opposite directions; inner arcs spin faster for depth.
_OUTER_MULT = 1.0    # clockwise
_MIDDLE_MULT = -1.3   # counter-clockwise, slightly faster
_INNER_MULT = 1.7     # clockwise, fastest

# SVG centre — all arcs pivot around this point.
_CX, _CY = 512, 512

# ── The project spiral mark as inline SVG ──────────────────────────────────
# Each arc gets its own ``rotate()`` transform around the centre so they can
# spin independently. The {ang_*} placeholders are filled per frame.

_SPINNER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" '
    'viewBox="0 0 1024 1024">'
    '<g fill="none" stroke="{color}" stroke-linecap="round">'
    '<path d="M 512 192 A 320 320 0 1 1 192 512" stroke-width="58"'
    ' transform="rotate({ang_outer}, 512, 512)"/>'
    '<path d="M 512 304 A 208 208 0 1 0 720 512" stroke-width="50" opacity="0.75"'
    ' transform="rotate({ang_middle}, 512, 512)"/>'
    '<path d="M 512 392 A 120 120 0 1 1 392 512" stroke-width="42" opacity="0.5"'
    ' transform="rotate({ang_inner}, 512, 512)"/>'
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
    """The DBAide spiral mark with counter-rotating arcs — crisp vector, HiDPI.

    *angle* is the base rotation in degrees; each arc derives its own angle
    from it via per-layer multipliers so they spin at different speeds in
    alternating directions.

    The ``**_kw`` signature keeps backward compatibility (``width`` is ignored).
    """
    dpr = _dpr()
    px_size = int(round(size * dpr))
    px = QPixmap(px_size, px_size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(dpr, dpr)
    svg_data = _SPINNER_SVG.format(
        color=color,
        ang_outer=angle * _OUTER_MULT,
        ang_middle=angle * _MIDDLE_MULT,
        ang_inner=angle * _INNER_MULT,
    )
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
