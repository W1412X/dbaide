"""Workflow history popup — on-demand, not a persistent side tab."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from dbaide.desktop.theme import Theme
from dbaide.desktop.views.history_tab import HistoryTab


class HistoryDialog(QDialog):
    history_selected = pyqtSignal(str)
    history_preview = pyqtSignal(str)
    history_delete = pyqtSignal(str)

    def __init__(self, history: HistoryTab, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflow History")
        self.setModal(False)
        self.resize(520, 560)
        self.setMinimumSize(400, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        self._history = history
        layout.addWidget(history, 1)

        history.history_selected.connect(self._on_open)
        history.history_preview.connect(self.history_preview.emit)
        history.history_delete.connect(self.history_delete.emit)

    def _on_open(self, workflow_id: str) -> None:
        self.history_selected.emit(workflow_id)
        self.hide()
