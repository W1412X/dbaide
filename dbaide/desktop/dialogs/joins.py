"""Saved join catalog popup."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout

from dbaide.desktop.theme import Theme
from dbaide.desktop.views.joins_tab import JoinsTab


from dbaide.desktop.window_chrome import ChromeDialog


class JoinsDialog(ChromeDialog):
    refresh_requested = pyqtSignal()
    add_requested = pyqtSignal(dict)
    update_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(str)

    def __init__(self, joins: JoinsTab, *, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self.setWindowTitle(t("menu.joins").rstrip("…"))
        self.setModal(False)
        self.resize(560, 520)
        self.setMinimumSize(440, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        self._joins = joins
        layout.addWidget(joins, 1)

        joins.refresh_requested.connect(self.refresh_requested.emit)
        joins.add_requested.connect(self.add_requested.emit)
        joins.update_requested.connect(self.update_requested.emit)
        joins.delete_requested.connect(self.delete_requested.emit)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_requested.emit()
