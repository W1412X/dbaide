"""Platform-specific Qt tweaks (keyboard focus, mnemonics, chrome widgets)."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QPushButton, QToolButton, QWidget


def chrome_suppresses_mnemonics() -> bool:
    """Windows and Linux assign Alt+letter shortcuts to labelled toolbar buttons."""
    return sys.platform in ("win32", "linux")


def escape_mnemonic(text: str) -> str:
    """Prevent Qt from treating ``&`` as an Alt shortcut marker in button labels."""
    return str(text or "").replace("&", "&&")


def configure_application(app: QApplication) -> None:
    """Global tweaks applied once before the main window is shown."""
    _ = app


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
