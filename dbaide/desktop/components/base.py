from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy


from dbaide.desktop.theme import Theme


def compact_button(
    text: str,
    *,
    primary: bool = False,
    width: int | None = None,
    parent=None,
) -> QPushButton:
    """Fixed-size action button — avoids macOS default-button blow-up."""
    btn = AgentButton(text, primary=primary, parent=parent)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(30)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if width is not None:
        btn.setFixedWidth(width)
    else:
        btn.setMinimumWidth(72)
        btn.setMaximumWidth(128)
    return btn


class Panel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("panel", True)


class Pill(QLabel):
    def __init__(self, text: str, color: str = Theme.BLUE, parent=None) -> None:
        super().__init__(text, parent)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"color:{color}; background:{Theme.PANEL_2}; border:1px solid {color}; "
            "border-radius:10px; padding:3px 10px; font-size:11px; font-weight:700;"
        )


class StatusBadge(Pill):
    COLORS = {
        "ready": Theme.GREEN,
        "running": Theme.BLUE,
        "idle": Theme.MUTED,
        "failed": Theme.RED,
        "warning": Theme.YELLOW,
        "building": Theme.BLUE,
        "missing": Theme.YELLOW,
    }

    def set_state(self, text: str, state: str = "idle") -> None:
        self.setText(text)
        self.set_color(self.COLORS.get(state, Theme.MUTED))


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("muted", True)


class AgentButton(QPushButton):
    def __init__(
        self,
        text: str,
        *,
        primary: bool = False,
        tab: bool = False,
        active: bool = False,
        parent=None,
    ) -> None:
        super().__init__(text, parent)
        if primary:
            self.setProperty("primary", True)
        if tab:
            self.setProperty("tab", True)
        if active:
            self.setProperty("active", True)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
