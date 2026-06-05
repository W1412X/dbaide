from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from dbaide.desktop.components.icons import svg_pixmap
from dbaide.desktop.theme import Theme

_COLUMN_WIDTH = 460


class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        body: str,
        actions: list | None = None,
        *,
        icon: str = "database",
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
        col_layout.setSpacing(8)
        col_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # A soft icon tile leads the empty state (Codex/Cursor style) — gives the
        # otherwise-bare column a calm focal point.
        if icon:
            tile = QLabel()
            tile.setFixedSize(52, 52)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setPixmap(svg_pixmap(icon, color=Theme.TEXT_2, size=26))
            tile.setStyleSheet(f"background: {Theme.PANEL_2}; border-radius: 14px;")
            col_layout.addWidget(tile, alignment=Qt.AlignmentFlag.AlignHCenter)
            col_layout.addSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(f"color: {Theme.TEXT}; font-size:18px; font-weight:600;")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._body_label = QLabel(body)
        self._body_label.setWordWrap(True)
        self._body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._body_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        col_layout.addWidget(self._title_label)
        col_layout.addWidget(self._body_label)
        layout.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_text(self, title: str, body: str) -> None:
        self._title_label.setText(title)
        self._body_label.setText(body)
