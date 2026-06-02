"""Small line icons for panel chrome (no emoji / fragile unicode glyphs)."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dbaide.desktop.theme import Theme


def _line_icon(size: int, draw: Callable[[QPainter, int], None], color: str = Theme.MUTED) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter, size)
    painter.end()
    return QIcon(pix)


def clock_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    def draw(p: QPainter, s: int) -> None:
        c = s / 2
        r = s / 2 - 2.5
        p.drawEllipse(int(c - r), int(c - r), int(2 * r), int(2 * r))
        p.drawLine(int(c), int(c), int(c), int(c - r * 0.55))
        p.drawLine(int(c), int(c), int(c + r * 0.5), int(c + r * 0.15))

    return _line_icon(size, draw, color)


def link_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    def draw(p: QPainter, s: int) -> None:
        p.drawRoundedRect(2, 4, 7, 7, 2, 2)
        p.drawRoundedRect(9, 7, 7, 7, 2, 2)

    return _line_icon(size, draw, color)


def more_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    def draw(p: QPainter, s: int) -> None:
        cy = s / 2
        for cx in (s * 0.28, s * 0.5, s * 0.72):
            p.setBrush(QColor(color))
            p.drawEllipse(int(cx - 1.5), int(cy - 1.5), 3, 3)

    return _line_icon(size, draw, color)


def panel_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    """A side panel: a rounded frame with a divided right column."""
    def draw(p: QPainter, s: int) -> None:
        p.drawRoundedRect(3, 4, s - 6, s - 8, 2, 2)
        x = int(s * 0.62)
        p.drawLine(x, 4, x, s - 4)

    return _line_icon(size, draw, color)


def plus_icon(*, color: str = Theme.MUTED, size: int = 18) -> QIcon:
    """A plus sign for create/new actions."""
    def draw(p: QPainter, s: int) -> None:
        c = s / 2
        r = s * 0.28
        p.drawLine(int(c - r), int(c), int(c + r), int(c))
        p.drawLine(int(c), int(c - r), int(c), int(c + r))

    return _line_icon(size, draw, color)


ICON_SIZE = QSize(16, 16)
