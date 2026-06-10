"""Native title-bar integration for all top-level Qt windows (main + dialogs)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QMargins, QMarginsF, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QDialog, QLayout, QWidget

from dbaide.desktop.theme import Theme

if TYPE_CHECKING:
    from dbaide.desktop.views.topbar import TopBar

TOPBAR_CONTENT_HEIGHT = 42
_TOPBAR_HPAD = 12


def supports_integrated_title_bar() -> bool:
    """Integrated title bar — macOS only.

    On Windows, ``ExpandedClientAreaHint`` + DWM caption tint draws the system
    title strip and client content on top of each other (visible ghosting). Linux
    support is still incomplete in Qt 6.9, so keep the native caption there too.
    """
    if sys.platform != "darwin":
        return False
    return hasattr(Qt.WindowType, "ExpandedClientAreaHint") and hasattr(
        Qt.WindowType, "NoTitleBarBackgroundHint"
    )


def topbar_layout_margins(
    safe: QMargins | QMarginsF,
    *,
    hpad: int = _TOPBAR_HPAD,
    content_height: int = TOPBAR_CONTENT_HEIGHT,
) -> tuple[int, int, int, int, int]:
    """Return (left, top, right, bottom, total_height) for ``TopBar`` layout.

    With ``ExpandedClientAreaHint``, Qt already offsets the central widget below
    the native title bar — do **not** add ``safe.top()`` again or the gap doubles.
    """
    left = hpad + int(safe.left())
    top = 0
    right = hpad + int(safe.right())
    bottom = 0
    height = content_height
    return left, top, right, bottom, height


def apply_window_background(window: QWidget) -> None:
    """Paint the top-level window with the active theme background."""
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    window.setAutoFillBackground(True)
    pal = window.palette()
    bg = QColor(Theme.BG)
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.Base, bg)
    window.setPalette(pal)
    _apply_windows_dwm_border(window, bg)


def _apply_windows_dwm_border(window: QWidget, bg: QColor) -> None:
    """Tint the outer Win10/11 frame only — never the caption (causes ghosting)."""
    if sys.platform != "win32":
        return
    wid = int(window.winId() or 0)
    if wid == 0:
        return
    try:
        import ctypes
        from ctypes import wintypes

        colorref = wintypes.DWORD((bg.blue() << 16) | (bg.green() << 8) | bg.red())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(wid),
            wintypes.DWORD(34),  # DWMWA_BORDER_COLOR
            ctypes.byref(colorref),
            ctypes.sizeof(colorref),
        )
    except Exception:
        return


def prepare_top_level_window(window: QWidget, *, clear_title: bool = False) -> bool:
    """Apply integrated title-bar flags and themed backdrop before ``show()``."""
    apply_window_background(window)
    if not supports_integrated_title_bar():
        return False
    window.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
    window.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
    if clear_title:
        window.setWindowTitle("")
    return True

def _base_layout_margins(layout: QLayout) -> tuple[int, int, int, int]:
    stored = layout.property("_dbaide_base_margins")
    if stored:
        return stored
    m = layout.contentsMargins()
    base = (m.left(), m.top(), m.right(), m.bottom())
    layout.setProperty("_dbaide_base_margins", base)
    return base


def sync_layout_safe_area(window: QWidget, layout: QLayout) -> None:
    """Inset dialog content away from caption / traffic-light controls."""
    handle = window.windowHandle()
    if handle is None:
        return
    safe = handle.safeAreaMargins()
    left, top, right, bottom = _base_layout_margins(layout)
    layout.setContentsMargins(
        left + int(safe.left()),
        top,
        right + int(safe.right()),
        bottom + int(safe.bottom()),
    )


def sync_topbar_safe_area(window: QWidget, topbar: TopBar) -> None:
    handle = window.windowHandle()
    if handle is None:
        return
    margins = handle.safeAreaMargins()
    left, top, right, bottom, height = topbar_layout_margins(margins)
    topbar.apply_safe_area(left, top, right, bottom, height)


def install_top_level_chrome(
    window: QWidget,
    *,
    layout: QLayout | None = None,
    topbar: TopBar | None = None,
) -> None:
    """Hook safe-area updates once ``windowHandle()`` exists (call from ``showEvent``)."""
    if not supports_integrated_title_bar():
        return

    def _sync() -> None:
        if topbar is not None:
            sync_topbar_safe_area(window, topbar)
        elif layout is not None:
            sync_layout_safe_area(window, layout)

    handle = window.windowHandle()
    if handle is None:
        QTimer.singleShot(0, _sync)
        return
    if layout is not None:
        _base_layout_margins(layout)
    handle.safeAreaMarginsChanged.connect(_sync)
    _sync()


class ChromeDialog(QDialog):
    """``QDialog`` with the same native title-bar treatment as the main window."""

    def __init__(self, parent: QWidget | None = None, *, clear_title: bool = False) -> None:
        super().__init__(parent)
        self._chrome_installed = False
        self._clear_title = clear_title
        prepare_top_level_window(self, clear_title=clear_title)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._chrome_installed:
            self._chrome_installed = True
            apply_window_background(self)
            install_top_level_chrome(self, layout=self.layout())
