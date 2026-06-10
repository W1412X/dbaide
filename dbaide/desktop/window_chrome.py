"""Native title-bar integration for all top-level Qt windows (main + dialogs)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QMargins, QMarginsF, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QDialog, QLayout, QMainWindow, QWidget

from dbaide.desktop.theme import Theme

if TYPE_CHECKING:
    from dbaide.desktop.views.topbar import TopBar

TOPBAR_CONTENT_HEIGHT = 42
_TOPBAR_HPAD = 12


def supports_integrated_title_bar() -> bool:
    """Qt 6.9+ expanded client area (macOS, Windows; best-effort on Linux)."""
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
    """Paint the full native window surface with the active theme (fixes light side gutters)."""
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    window.setAutoFillBackground(True)
    if sys.platform == "win32":
        window.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
    pal = window.palette()
    bg = QColor(Theme.BG)
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.Base, bg)
    window.setPalette(pal)
    _apply_windows_dwm_colors(window, bg)


def _apply_windows_dwm_colors(window: QWidget, bg: QColor) -> None:
    """Match Win10/11 outer border and caption strip to the app background."""
    if sys.platform != "win32":
        return
    wid = int(window.winId() or 0)
    if wid == 0:
        return
    try:
        import ctypes
        from ctypes import wintypes

        # COLORREF 0x00BBGGRR
        def _cref(c: QColor) -> wintypes.DWORD:
            return wintypes.DWORD((c.blue() << 16) | (c.green() << 8) | c.red())

        dwm = ctypes.windll.dwmapi
        for attr, color in (
            (34, bg),           # DWMWA_BORDER_COLOR
            (35, bg),           # DWMWA_CAPTION_COLOR
            (36, QColor(Theme.TEXT)),  # DWMWA_TEXT_COLOR
        ):
            cref = _cref(color)
            dwm.DwmSetWindowAttribute(
                wintypes.HWND(wid),
                wintypes.DWORD(attr),
                ctypes.byref(cref),
                ctypes.sizeof(cref),
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


def sync_central_widget_edges(window: QMainWindow) -> None:
    """Cancel horizontal safe-area gutter on Windows so content is edge-to-edge."""
    if sys.platform != "win32" or not supports_integrated_title_bar():
        return
    central = window.centralWidget()
    if central is None:
        return
    layout = central.layout()
    if layout is None:
        return
    handle = window.windowHandle()
    if handle is None:
        return
    safe = handle.safeAreaMargins()
    sl, sr = int(safe.left()), int(safe.right())
    if sl == 0 and sr == 0:
        return
    left, top, right, bottom = _base_layout_margins(layout)
    layout.setContentsMargins(left - sl, top, right - sr, bottom)


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
        apply_window_background(window)
        if isinstance(window, QMainWindow):
            sync_central_widget_edges(window)
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
    if isinstance(window, QMainWindow):
        central = window.centralWidget()
        if central is not None and central.layout() is not None:
            _base_layout_margins(central.layout())
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
            install_top_level_chrome(self, layout=self.layout())
