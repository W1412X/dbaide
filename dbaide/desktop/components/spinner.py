"""A tiny shared "busy" animator.

Drives a spinning-circle glyph for any consumer (a button label, a tree row)
off a single QTimer. Text-based so it works equally in a QPushButton and inside
a QTreeWidget item — no QMovie/asset needed.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

# A rotating circle — reads as a loading spinner at any font.
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


class BusyAnimator(QObject):
    """Calls ``on_frame(frame)`` on a timer while started; cheap to start/stop."""

    def __init__(self, on_frame: Callable[[str], None], *, interval_ms: int = 120, parent=None) -> None:
        super().__init__(parent)
        self._on_frame = on_frame
        self._i = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def frame(self) -> str:
        return SPINNER_FRAMES[self._i % len(SPINNER_FRAMES)]

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if not self._timer.isActive():
            self._i = 0
            self._on_frame(self.frame)
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(SPINNER_FRAMES)
        self._on_frame(self.frame)
