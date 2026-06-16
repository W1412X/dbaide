"""Compact icon-only tool buttons for panel chrome."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QToolButton

from dbaide.desktop.theme import Theme


class IconToolButton(QToolButton):
    """Compact icon-only action with a quiet hover surface."""

    def __init__(self, icon: QIcon, tooltip: str, *, parent=None) -> None:
        super().__init__(parent)
        self._base_icon = icon
        self.setIcon(icon)
        self.setIconSize(QSize(12, 12))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setAutoRaise(True)
        self.setFixedSize(18, 18)
        self._apply_style()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 5px;
                padding: 0;
                margin: 0;
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_2};
            }}
            QToolButton:pressed {{
                background: {Theme.PANEL_3};
            }}
            """
        )
