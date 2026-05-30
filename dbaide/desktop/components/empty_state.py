from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from dbaide.desktop.components.inputs import configure_wrapped_label
from dbaide.desktop.theme import Theme


class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        body: str,
        actions: list | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        _ = actions
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:20px;font-weight:800;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label = QLabel(body)
        configure_wrapped_label(body_label, max_width=520)
        body_label.setStyleSheet(f"color: {Theme.MUTED};")
        body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        layout.addWidget(body_label)
