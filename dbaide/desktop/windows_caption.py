"""Windows-only custom caption bar with theme-aware gray window controls."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, Qt, QSize
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.platform_ui import configure_chrome_button
from dbaide.desktop.theme import Theme

if TYPE_CHECKING:
    pass

CAPTION_HEIGHT = 32
_RESIZE_BORDER = 8
_BTN_WIDTH = 46

# Win32 WM_NCHITTEST return codes
_HTCLIENT = 1
_HTCAPTION = 2
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17


def uses_windows_custom_caption() -> bool:
    return sys.platform == "win32"


def _button_stylesheet() -> str:
    return f"""
    QToolButton {{
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 0;
        min-width: {_BTN_WIDTH}px;
        max-width: {_BTN_WIDTH}px;
        min-height: {CAPTION_HEIGHT}px;
        max-height: {CAPTION_HEIGHT}px;
    }}
    QToolButton:hover {{
        background: {Theme.PANEL_2};
    }}
    QToolButton:pressed {{
        background: {Theme.PANEL_3};
    }}
    """


class WindowsCaptionBar(QWidget):
    """Frameless caption strip: title + gray minimize / maximize / close."""

    def __init__(self, window: QWidget, *, title: str = "", parent=None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setObjectName("windowsCaptionBar")
        self.setFixedHeight(CAPTION_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._apply_bar_style()

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(0)

        self._title = QLabel(title or window.windowTitle())
        self._title.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 12px; background: transparent; padding: 0;"
        )
        row.addWidget(self._title, 1)

        icon_color = Theme.MUTED
        self._min = self._make_btn("minimize", icon_color, window.showMinimized)
        self._max = self._make_btn("maximize", icon_color, self._toggle_maximize)
        self._close = self._make_btn("x", icon_color, window.close)
        for btn in (self._min, self._max, self._close):
            row.addWidget(btn, 0)

        window.installEventFilter(self)

    def _apply_bar_style(self) -> None:
        self.setStyleSheet(
            f"QWidget#windowsCaptionBar {{ background-color: {Theme.BG}; border: none; }}"
        )

    def refresh_theme(self) -> None:
        self._apply_bar_style()
        icon_color = Theme.MUTED
        for btn, name in ((self._min, "minimize"), (self._max, self._max_icon_name()), (self._close, "x")):
            btn.setIcon(svg_icon(name, color=icon_color, size=12))
            btn.setStyleSheet(_button_stylesheet())
        self._title.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 12px; background: transparent; padding: 0;"
        )

    def _max_icon_name(self) -> str:
        return "restore" if self._window.isMaximized() else "maximize"

    def _make_btn(self, icon_name: str, color: str, slot) -> QToolButton:
        btn = QToolButton(self)
        btn.setIcon(svg_icon(icon_name, color=color, size=12))
        btn.setIconSize(QSize(12, 12))
        btn.setStyleSheet(_button_stylesheet())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        configure_chrome_button(btn)
        btn.clicked.connect(slot)
        return btn

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._max.setIcon(svg_icon(self._max_icon_name(), color=Theme.MUTED, size=12))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._window and event.type() == QEvent.Type.WindowStateChange:
            self._max.setIcon(svg_icon(self._max_icon_name(), color=Theme.MUTED, size=12))
        return False


class _Win32ResizeFilter(QAbstractNativeEventFilter):
    def __init__(self, window: QWidget, caption_height: int) -> None:
        super().__init__()
        self._window = window
        self._caption_h = caption_height
        self._border = _RESIZE_BORDER

    def nativeEventFilter(self, event_type: bytes | bytearray | memoryview, message: int) -> tuple[bool, int]:
        if sys.platform != "win32" or bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        try:
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message != 0x0084:  # WM_NCHITTEST
                return False, 0
            win = self._window
            if win is None or not win.isVisible():
                return False, 0
            x = ctypes.c_int16(msg.lParam & 0xFFFF).value
            y = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
            geo = win.frameGeometry()
            rx, ry = x - geo.x(), y - geo.y()
            ww, wh = geo.width(), geo.height()
            b = self._border
            if win.isMaximized():
                return False, 0
            if rx < b and ry < b:
                return True, _HTTOPLEFT
            if rx >= ww - b and ry < b:
                return True, _HTTOPRIGHT
            if rx < b and ry >= wh - b:
                return True, _HTBOTTOMLEFT
            if rx >= ww - b and ry >= wh - b:
                return True, _HTBOTTOMRIGHT
            if rx < b:
                return True, _HTLEFT
            if rx >= ww - b:
                return True, _HTRIGHT
            if ry < b:
                return True, _HTTOP
            if ry >= wh - b:
                return True, _HTBOTTOM
            if ry < self._caption_h:
                return True, _HTCAPTION
        except Exception:
            return False, 0
        return False, 0


_filters: dict[int, _Win32ResizeFilter] = {}


def register_frameless_resize(window: QWidget, *, caption_height: int = CAPTION_HEIGHT) -> None:
    if not uses_windows_custom_caption():
        return
    app = QApplication.instance()
    if app is None:
        return
    wid = int(window.winId() or 0)
    if wid == 0:
        return
    old = _filters.pop(wid, None)
    if old is not None:
        app.removeNativeEventFilter(old)
    filt = _Win32ResizeFilter(window, caption_height)
    _filters[wid] = filt
    app.installNativeEventFilter(filt)


def enable_windows_frameless(window: QWidget) -> None:
    if not uses_windows_custom_caption():
        return
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.setProperty("_dbaide_win_caption", True)


def _repack_layout_with_caption(window: QWidget, caption: WindowsCaptionBar) -> None:
    old = window.layout()
    if old is None:
        outer = QVBoxLayout(window)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(caption)
        return

    items: list = []
    margins = old.contentsMargins()
    spacing = old.spacing()
    while old.count():
        items.append(old.takeAt(0))

    old.setContentsMargins(0, 0, 0, 0)
    old.setSpacing(0)
    old.addWidget(caption)

    content = QWidget()
    content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    inner = QVBoxLayout(content)
    inner.setContentsMargins(margins)
    inner.setSpacing(spacing)
    for item in items:
        if item.widget():
            inner.addWidget(item.widget())
        elif item.layout():
            inner.addLayout(item.layout())
        elif item.spacerItem():
            inner.addItem(item.spacerItem())
    old.addWidget(content, 1)


def attach_windows_caption(window: QWidget, *, title: str = "") -> WindowsCaptionBar | None:
    """Replace native caption with gray custom controls (Windows only)."""
    if not uses_windows_custom_caption():
        return None
    if window.property("_dbaide_win_caption_attached"):
        bar = window.findChild(WindowsCaptionBar)
        return bar if isinstance(bar, WindowsCaptionBar) else None

    enable_windows_frameless(window)
    window.setProperty("_dbaide_win_caption_attached", True)
    caption = WindowsCaptionBar(window, title=title or window.windowTitle())
    _repack_layout_with_caption(window, caption)
    register_frameless_resize(window)
    return caption
