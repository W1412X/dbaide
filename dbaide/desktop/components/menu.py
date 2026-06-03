from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFontMetrics, QIcon
from PyQt6.QtWidgets import QMenu, QSizePolicy, QToolButton

from dbaide.desktop.theme import Theme


def _style_menu(menu: QMenu) -> None:
    menu.setStyleSheet(
        f"""
        QMenu {{
            background: {Theme.PANEL};
            color: {Theme.TEXT};
            border: 1px solid {Theme.BORDER};
            border-radius: 10px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 28px 8px 14px;
            border-radius: 6px;
            min-height: 20px;
        }}
        QMenu::item:selected {{
            background: {Theme.PANEL_3};
        }}
        QMenu::separator {{
            height: 1px;
            background: {Theme.BORDER_SOFT};
            margin: 4px 8px;
        }}
        QMenu::right-arrow {{
            width: 8px;
            height: 8px;
            margin-right: 8px;
        }}
        """
    )


class MenuButton(QToolButton):
    """Single trigger that opens a dropdown menu."""

    def __init__(
        self,
        text: str = "⋯",
        *,
        parent=None,
        max_width: int = 0,
        icon: QIcon | None = None,
        tooltip: str = "",
        icon_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._max_width = max_width
        self._icon_only = icon_only
        if icon_only and icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(15, 15))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.setFixedSize(26, 26)
            self.setAutoRaise(True)
            if tooltip:
                self.setToolTip(tooltip)
        else:
            self.setText(text)
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.setFixedHeight(26)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        _style_menu(self._menu)
        self.setMenu(self._menu)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if max_width and not icon_only:
            self.setMaximumWidth(max_width)
        self._apply_style(pill=False, icon_only=icon_only)

    def _apply_style(self, *, pill: bool, icon_only: bool = False) -> None:
        if icon_only:
            # Visible soft fill (not transparent), no border — reads as a button.
            self.setStyleSheet(
                f"""
                QToolButton {{
                    background: {Theme.PANEL_2};
                    border: none;
                    border-radius: 7px;
                    padding: 0;
                }}
                QToolButton:hover {{
                    background: {Theme.PANEL_3};
                }}
                QToolButton:pressed {{
                    background: {Theme.BORDER};
                }}
                QToolButton::menu-indicator {{
                    image: none;
                    width: 0px;
                }}
                """
            )
            return
        radius = 16 if pill else 8
        bg = "transparent" if pill else Theme.PANEL_2
        border = Theme.BORDER_SOFT if pill else Theme.BORDER
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {bg};
                color: {Theme.TEXT_2};
                border: 1px solid {border};
                border-radius: {radius}px;
                padding: 0px 12px;
                font-size: 12px;
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_2};
                color: {Theme.TEXT};
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            """
        )

    def setText(self, text: str) -> None:
        if self._icon_only:
            return
        self._full_text = text
        super().setText(self._elided(text))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._icon_only or not self._max_width:
            return
        super().setText(self._elided(self._full_text))

    def _elided(self, text: str) -> str:
        if not self._max_width:
            return text
        width = max(self._max_width - 20, 40)
        return QFontMetrics(self.font()).elidedText(text, Qt.TextElideMode.ElideRight, width)

    def add_action(self, label: str, callback) -> None:
        action = self._menu.addAction(label)
        action.triggered.connect(callback)

    def add_separator(self) -> None:
        self._menu.addSeparator()


class PillSelect(QToolButton):
    """Codex-style compact context pill with inline dropdown."""

    value_changed = pyqtSignal(str)

    def __init__(self, placeholder: str = "Select", *, parent=None, max_width: int = 140) -> None:
        super().__init__(parent)
        self._placeholder = placeholder
        self._max_width = max_width
        self._options: list[tuple[str, str]] = []
        self._tooltips: dict[str, str] = {}
        self._value = ""
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        _style_menu(self._menu)
        self.setMenu(self._menu)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setMaximumWidth(max_width)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        # Soft selector — a subtle visible fill (no border, so it doesn't double up
        # with the composer's own border) makes it clearly an interactive pill rather
        # than plain text; brightens on hover.
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {Theme.PANEL_2};
                color: {Theme.TEXT_2};
                border: none;
                border-radius: 8px;
                padding: 0px 10px;
                font-size: 12px;
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_3};
                color: {Theme.TEXT};
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            """
        )
        self._sync_label()

    def set_option_tooltips(self, tooltips: dict[str, str]) -> None:
        self._tooltips = dict(tooltips)
        self._rebuild_menu()
        self._sync_label()

    def set_options(self, options: list[tuple[str, str]]) -> None:
        self._options = list(options)
        self._rebuild_menu()
        if self._value and not any(v == self._value for _, v in self._options):
            self._value = self._options[0][1] if self._options else ""
        self._sync_label()

    def set_value(self, value: str) -> None:
        self._value = value
        self._sync_label()

    def value(self) -> str:
        return self._value

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        for label, value in self._options:
            action = self._menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == self._value)
            if tip := self._tooltips.get(value):
                action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, v=value: self._pick(v))

    def _pick(self, value: str) -> None:
        self._value = value
        self._rebuild_menu()
        self._sync_label()
        self.value_changed.emit(value)

    def _sync_label(self) -> None:
        label = self._placeholder
        for item_label, item_value in self._options:
            if item_value == self._value:
                label = item_label
                break
        text = f"{label}  ▾"
        width = max(self._max_width - 16, 48)
        self.setText(QFontMetrics(self.font()).elidedText(text, Qt.TextElideMode.ElideRight, width))
        self.setToolTip(self._tooltips.get(self._value, ""))
