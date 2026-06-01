"""A shared "busy" spinner.

Paints a smoothly rotating arc (a real loading circle) as a QIcon/QPixmap, driven
off a single QTimer. Used for the trace's running rows and for action buttons —
an icon never clips the button label the way an inline glyph did, and rotating by
a small angle each tick looks smooth instead of stepping through 4 glyphs.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dbaide.desktop.theme import Theme

# Degrees advanced per tick — 30° × ~70ms ≈ a smooth ~0.8s revolution.
_ANGLE_STEP = 30
# Kept for any text-only consumer (e.g. plain-text logs).
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


def spinner_pixmap(angle: float, *, size: int = 14, color: str = Theme.BLUE, width: float = 2.0) -> QPixmap:
    """A 270° arc rotated to ``angle`` — the classic spinning ring."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    margin = width
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    # Qt angles are in 1/16°, counter-clockwise; negate for a clockwise spin.
    painter.drawArc(rect, int(-angle * 16), int(270 * 16))
    painter.end()
    return pix


def spinner_icon(angle: float, *, size: int = 14, color: str = Theme.BLUE, width: float = 2.0) -> QIcon:
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
    def frame(self) -> str:
        return SPINNER_FRAMES[int(self._angle // 90) % len(SPINNER_FRAMES)]

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
