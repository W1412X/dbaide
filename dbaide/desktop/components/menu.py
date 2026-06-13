from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFontMetrics, QIcon
from PyQt6.QtWidgets import QMenu, QSizePolicy, QToolButton

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme, menu_stylesheet
from dbaide.desktop.platform_ui import configure_chrome_button, escape_mnemonic


def _apply_menu_style(menu: QMenu) -> None:
    """Opaque, frameless popup — avoids macOS translucent ghosting in light mode."""
    menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    menu.setAutoFillBackground(True)
    menu.setStyleSheet(menu_stylesheet())
    menu.update()


def _style_menu(menu: QMenu) -> None:
    _apply_menu_style(menu)
    if menu.property("_dbaide_menu_hook"):
        return
    menu.setProperty("_dbaide_menu_hook", True)
    menu.aboutToShow.connect(lambda: _apply_menu_style(menu))


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
        filled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._max_width = max_width
        self._icon_only = icon_only
        self._filled = filled
        if icon_only and icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(16, 16))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.setFixedSize(30, 30)
            self.setAutoRaise(True)
            if tooltip:
                self.setToolTip(tooltip)
        else:
            self.setText(text)
            if icon is not None:
                self.setIcon(icon)
                self.setIconSize(QSize(15, 15))
                self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            else:
                self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.setFixedHeight(28 if filled else 26)
            if tooltip:
                self.setToolTip(tooltip)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        _style_menu(self._menu)
        self.setMenu(self._menu)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        configure_chrome_button(self)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if max_width and not icon_only:
            self.setMaximumWidth(max_width)
        self._apply_style(pill=False, icon_only=icon_only)

    def _apply_style(self, *, pill: bool, icon_only: bool = False) -> None:
        if icon_only:
            self.setStyleSheet(
                f"""
                QToolButton {{
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    padding: 0;
                }}
                QToolButton:hover {{
                    background: {Theme.PANEL_2};
                }}
                QToolButton:pressed {{
                    background: {Theme.PANEL_3};
                }}
                QToolButton::menu-indicator {{
                    image: none;
                    width: 0px;
                }}
                """
            )
            return
        radius = 8 if self._filled else 7
        bg = Theme.PANEL_2 if self._filled else "transparent"
        border = "transparent"
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {bg};
                color: {Theme.TEXT_2};
                border: 1px solid {border};
                border-radius: {radius}px;
                padding: 0px {12 if self._filled else 10}px;
                font-size: {13 if self._filled else 12}px;
                font-weight: {650 if self._filled else 500};
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_3 if self._filled else Theme.PANEL_2};
                color: {Theme.TEXT};
            }}
            QToolButton:pressed {{
                background: {Theme.BORDER if self._filled else Theme.PANEL_3};
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
        super().setText(self._elided(escape_mnemonic(text)))

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

    def __init__(
        self,
        placeholder: str = "Select",
        *,
        parent=None,
        max_width: int = 140,
        soft: bool = False,
    ) -> None:
        super().__init__(parent)
        self._placeholder = placeholder
        self._max_width = max_width
        self._soft = soft
        self._options: list[tuple[str, str]] = []
        self._tooltips: dict[str, str] = {}
        self._value = ""
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        _style_menu(self._menu)
        self._menu.aboutToShow.connect(self._on_menu_show)
        self._menu.aboutToHide.connect(self._on_menu_hide)
        self.setMenu(self._menu)
        configure_chrome_button(self)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setIcon(svg_icon("chevron-down", color=Theme.TEXT_2, size=12))
        self.setIconSize(QSize(12, 12))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._apply_style()
        self._sync_label()

    def _on_menu_show(self) -> None:
        _apply_menu_style(self._menu)
        self._apply_style(open_menu=True)
        self.setIcon(svg_icon("chevron-up", color=Theme.TEXT_2, size=12))

    def _on_menu_hide(self) -> None:
        self._apply_style(open_menu=False)
        self.setIcon(svg_icon("chevron-down", color=Theme.TEXT_2, size=12))

    def _apply_style(self, *, open_menu: bool = False) -> None:
        radius = Theme.RADIUS_MD
        if self._soft:
            open_bg = Theme.PANEL_2
            self.setStyleSheet(
                f"""
                QToolButton {{
                    background: {open_bg if open_menu else "transparent"};
                    color: {Theme.TEXT if open_menu else Theme.TEXT_2};
                    border: 1px solid transparent;
                    border-radius: 7px;
                    padding: 0px 8px 0px 10px;
                    font-size: 12px;
                }}
                QToolButton:hover {{
                    background: {Theme.PANEL_2};
                    border: 1px solid transparent;
                    color: {Theme.TEXT};
                }}
                QToolButton:focus {{
                    outline: none;
                    border: 1px solid {Theme.BORDER_SOFT};
                }}
                QToolButton::menu-indicator {{
                    image: none;
                    width: 0px;
                }}
                """
            )
            return
        open_bg = Theme.PANEL_2 if open_menu else "transparent"
        self.setStyleSheet(
            f"""
            QToolButton {{
                background: {open_bg};
                color: {Theme.TEXT if open_menu else Theme.TEXT_2};
                border: none;
                border-radius: 7px;
                padding: 0px 10px;
                font-size: 12px;
            }}
            QToolButton:hover {{
                background: {Theme.PANEL_2};
                color: {Theme.TEXT};
            }}
            QToolButton:pressed {{
                background: {Theme.PANEL_3};
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            """
        )

    def current_value(self) -> str:
        return self._value

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
        self._rebuild_menu()
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
        fm = QFontMetrics(self.font())
        icon_pad = 22
        h_pad = 20
        natural = fm.horizontalAdvance(label) + icon_pad + h_pad
        width = min(max(natural, 72), self._max_width)
        self.setFixedWidth(width)
        text = fm.elidedText(escape_mnemonic(label), Qt.TextElideMode.ElideRight, width - icon_pad - 4)
        self.setText(text)
        self.setToolTip(self._tooltips.get(self._value, label))
