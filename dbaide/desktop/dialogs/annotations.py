"""User annotations (notes) manager popup."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from dbaide.desktop.theme import Theme
from dbaide.desktop.views.annotations_tab import AnnotationsTab
from dbaide.i18n import t as _t


class AnnotationsDialog(QDialog):
    refresh_requested = pyqtSignal()
    add_requested = pyqtSignal(dict)
    update_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(str)

    def __init__(self, annotations: AnnotationsTab, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("notes.title"))
        self.setModal(False)
        self.resize(620, 520)
        self.setMinimumSize(460, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        self._annotations = annotations
        layout.addWidget(annotations, 1)

        annotations.refresh_requested.connect(self.refresh_requested.emit)
        annotations.add_requested.connect(self.add_requested.emit)
        annotations.update_requested.connect(self.update_requested.emit)
        annotations.delete_requested.connect(self.delete_requested.emit)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_requested.emit()
