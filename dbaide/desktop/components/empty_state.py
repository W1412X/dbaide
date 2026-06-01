from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from dbaide.desktop.theme import Theme

_COLUMN_WIDTH = 460


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

        # A fixed-width centred column gives word-wrap a deterministic width, so the
        # body's height-for-width is computed correctly and longer/translated text is
        # never clipped to one line.
        column = QWidget()
        column.setFixedWidth(_COLUMN_WIDTH)
        col_layout = QVBoxLayout(column)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size:20px;font-weight:800;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        col_layout.addWidget(title_label)
        col_layout.addWidget(body_label)
        layout.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)
