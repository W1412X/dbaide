"""Platform-specific Qt tweaks (keyboard focus, mnemonics, chrome widgets)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QMargins, QMarginsF, Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QPushButton, QToolButton, QWidget

if TYPE_CHECKING:
    from dbaide.desktop.views.topbar import TopBar

# Height of the in-app header row below the OS window controls.
TOPBAR_CONTENT_HEIGHT = 42
_TOPBAR_HPAD = 12


def chrome_suppresses_mnemonics() -> bool:
    """Windows and Linux assign Alt+letter shortcuts to labelled toolbar buttons."""
    return sys.platform in ("win32", "linux")


def escape_mnemonic(text: str) -> str:
    """Prevent Qt from treating ``&`` as an Alt shortcut marker in button labels."""
    return str(text or "").replace("&", "&&")


def supports_integrated_title_bar() -> bool:
    """Qt 6.9+ can extend client area into the native title bar (macOS/Windows)."""
    return (
        sys.platform in ("darwin", "win32")
        and hasattr(Qt.WindowType, "ExpandedClientAreaHint")
        and hasattr(Qt.WindowType, "NoTitleBarBackgroundHint")
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


def configure_application(app: QApplication) -> None:
    """Global tweaks applied once before the main window is shown."""
    _ = app


def apply_window_chrome_flags(window: QWidget) -> bool:
    """Extend the app background into the OS title bar; keep native window controls."""
    if not supports_integrated_title_bar():
        return False
    window.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
    window.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
    # Brand lives in TopBar — avoid duplicating "DBAide" in the system caption.
    window.setWindowTitle("")
    return True


def sync_topbar_safe_area(window: QWidget, topbar: TopBar) -> None:
    """Inset TopBar controls away from traffic lights / caption buttons."""
    handle = window.windowHandle()
    if handle is None:
        return
    margins = handle.safeAreaMargins()
    left, top, right, bottom, height = topbar_layout_margins(margins)
    topbar.apply_safe_area(left, top, right, bottom, height)


def install_window_chrome(window: QWidget, topbar: TopBar) -> None:
    """Hook safe-area updates so the header clears native window controls."""
    if not supports_integrated_title_bar():
        return

    def _sync() -> None:
        sync_topbar_safe_area(window, topbar)

    handle = window.windowHandle()
    if handle is None:
        QTimer.singleShot(0, _sync)
        return
    handle.safeAreaMarginsChanged.connect(_sync)
    _sync()


def configure_chrome_button(btn: QToolButton | QPushButton) -> None:
    """Keep header/toolbar controls from stealing letter keys (Alt mnemonics / focus)."""
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if isinstance(btn, QPushButton):
        btn.setAutoDefault(False)
        btn.setDefault(False)
    btn.setShortcut(QKeySequence())


def label_for_chrome_button(text: str, *, icon_only: bool) -> str:
    """Return button label text safe for the current platform."""
    if icon_only or not text:
        return ""
    if chrome_suppresses_mnemonics():
        return ""
    return escape_mnemonic(text)
