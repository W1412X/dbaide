"""Compact icon-only tool buttons for panel chrome."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QToolButton

from dbaide.desktop.theme import Theme


class IconToolButton(QToolButton):
    """Square soft icon button — a subtle but visible fill (no border) carries the
    button shape, so it reads as a control rather than an invisible hotspot."""

    def __init__(self, icon: QIcon, tooltip: str, *, parent=None) -> None:
        super().__init__(parent)
        self._base_icon = icon
        self.setIcon(icon)
        self.setIconSize(QSize(15, 15))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setAutoRaise(True)
        self.setFixedSize(26, 26)
        self._apply_style()

    def _apply_style(self) -> None:
        # Visible soft fill (not transparent), no border; brightens on hover.
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {Theme.PANEL_2};
                border: none;
                border-radius: 7px;
                padding: 0;
                margin: 0;
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_3};
            }}
            QToolButton:pressed {{
                background: {Theme.BORDER};
            }}
            """
        )
