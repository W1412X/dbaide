"""Native title-bar integration for all top-level Qt windows (main + dialogs)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEasingCurve, QMargins, QMarginsF, QPropertyAnimation, Qt, QTimer
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
    _apply_windows_dwm_theme(window)


def _colorref(color: QColor) -> int:
    return (color.blue() << 16) | (color.green() << 8) | color.red()


def _apply_windows_dwm_theme(window: QWidget) -> None:
    """Sync Win10/11 caption strip + border with the app light/dark theme.

    Safe on Windows because we do **not** use ``ExpandedClientAreaHint`` there —
    client content sits below the native caption, so tinting the caption does not
    overlap the in-app TopBar (unlike the v0.1.1 ghosting bug).
    """
    if sys.platform != "win32":
        return
    wid = int(window.winId() or 0)
    if wid == 0:
        return
    try:
        import ctypes
        from ctypes import wintypes

        from dbaide.desktop.theme import Theme, current_theme_name

        dark = current_theme_name() == "dark"
        bg = QColor(Theme.BG)
        text = QColor(Theme.TEXT)
        dwm = ctypes.windll.dwmapi
        hwnd = wintypes.HWND(wid)

        def _set_dword(attr: int, value: int) -> None:
            v = wintypes.DWORD(value)
            dwm.DwmSetWindowAttribute(
                hwnd, wintypes.DWORD(attr), ctypes.byref(v), ctypes.sizeof(v)
            )

        def _set_color(attr: int, color: QColor) -> None:
            v = wintypes.DWORD(_colorref(color))
            dwm.DwmSetWindowAttribute(
                hwnd, wintypes.DWORD(attr), ctypes.byref(v), ctypes.sizeof(v)
            )

        # 19 = Win10 1809+, 20 = Win11 — enable/disable dark caption chrome.
        for attr in (19, 20):
            try:
                _set_dword(attr, 1 if dark else 0)
            except Exception:
                pass
        _set_color(34, bg)    # DWMWA_BORDER_COLOR
        _set_color(35, bg)    # DWMWA_CAPTION_COLOR
        _set_color(36, text)  # DWMWA_TEXT_COLOR
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


def sync_dialog_minimum_size(window: QWidget) -> None:
    """Ensure dialog height fits its content after safe-area insets apply."""
    layout = window.layout()
    if layout is None:
        return
    layout.activate()
    hint = layout.sizeHint()
    extra = 0
    handle = window.windowHandle()
    if handle is not None:
        safe = handle.safeAreaMargins()
        extra = int(safe.top()) + int(safe.bottom())
    min_h = hint.height() + extra
    min_w = hint.width()
    if min_w > 0 and window.minimumWidth() < min_w:
        window.setMinimumWidth(min_w)
    if window.minimumHeight() < min_h:
        window.setMinimumHeight(min_h)
    if window.height() < min_h:
        window.resize(max(window.width(), window.minimumWidth()), min_h)


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
        if isinstance(window, QDialog):
            sync_dialog_minimum_size(window)

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
            self._play_open_fade()

    def _play_open_fade(self) -> None:
        """Subtle fade-in on first open. Guarded so the dialog can never get stuck
        transparent: a fallback timer (and the animation's finished signal) always
        restore full opacity even if the animation is interrupted."""
        try:
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(130)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: self.setWindowOpacity(1.0))
            self._open_fade = anim  # keep a reference so it isn't GC'd mid-flight
            anim.start()
            # Belt-and-suspenders: force full opacity shortly after, regardless.
            QTimer.singleShot(220, lambda: self.setWindowOpacity(1.0))
        except Exception:
            self.setWindowOpacity(1.0)
