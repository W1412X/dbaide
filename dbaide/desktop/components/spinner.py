"""A shared "busy" spinner — a crisp, smoothly rotating loader ring.

The ring is the Lucide "loader" arc rendered as a vector (via the SVG icon system)
at the screen's device-pixel-ratio, so it stays sharp on HiDPI instead of the soft
pixels a small hand-painted arc produced. Driven off a single QTimer; rotating a
few degrees per tick looks smooth rather than stepping through glyphs.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QIcon, QPixmap

from dbaide.desktop.components.icons import svg_pixmap
from dbaide.desktop.theme import Theme

# Degrees advanced per tick — 30° × ~70ms ≈ a smooth ~0.8s revolution.
_ANGLE_STEP = 30
# Logical px — always pair with ``widget.setIconSize(QSize(SPINNER_SIZE, SPINNER_SIZE))``.
SPINNER_SIZE = 15


def spinner_pixmap(angle: float, *, size: int = SPINNER_SIZE, color: str = Theme.BLUE, width: float = 2.0) -> QPixmap:
    """The loader arc rotated to ``angle`` — crisp vector, HiDPI-aware."""
    return svg_pixmap("loader", color=color, size=size, width=width, angle=angle)


def spinner_icon(angle: float, *, size: int = SPINNER_SIZE, color: str = Theme.BLUE, width: float = 2.0) -> QIcon:
    return QIcon(spinner_pixmap(angle, size=size, color=color, width=width))


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
