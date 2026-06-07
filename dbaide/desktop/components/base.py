from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy


from dbaide.desktop.theme import Theme


def ghost_action_button(
    text: str, *, icon: QIcon | None = None, tooltip: str = "", parent=None
) -> QPushButton:
    """A low-profile inline action: small icon + label, no border, muted until
    hover (Codex/Claude message-action style). For the row of actions under an
    answer (Copy SQL, Open in SQL, …)."""
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(26)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if icon is not None:
        btn.setIcon(icon)
        btn.setIconSize(QSize(14, 14))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent;
            color: {Theme.MUTED};
            border: none;
            border-radius: 6px;
            padding: 0 8px;
            font-size: 12px;
            text-align: left;
        }}
        QPushButton:hover {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}
        QPushButton:pressed {{ background: {Theme.PANEL_3}; }}
        """
    )
    return btn


def compact_button(
    text: str,
    *,
    primary: bool = False,
    icon: QIcon | None = None,
    tooltip: str = "",
    width: int | None = None,
    parent=None,
) -> QPushButton:
    """Fixed-size action button — avoids macOS default-button blow-up."""
    btn = AgentButton(text, primary=primary, parent=parent)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(26)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if icon is not None:
        btn.setIcon(icon)
        btn.setIconSize(QSize(14, 14))
    if tooltip:
        btn.setToolTip(tooltip)
    if width is not None:
        btn.setFixedWidth(width)
    else:
        btn.adjustSize()
        btn.setFixedWidth(max(btn.sizeHint().width(), 72))
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
        # A small colored status dot + muted label on a quiet pill — calmer than a
        # fully color-outlined badge, the way AI IDEs show status.
        color = self.COLORS.get(state, Theme.MUTED)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(
            f"<span style='color:{color}; font-size:10px;'>●</span>"
            f"&nbsp;<span style='color:{Theme.TEXT_2};'>{text}</span>"
        )
        self.setFixedHeight(30)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(
            f"background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};"
            " border-radius:10px; padding:0 12px; font-size:12px; font-weight:600;"
        )


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
