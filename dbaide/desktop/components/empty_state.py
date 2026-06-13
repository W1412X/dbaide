from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_pixmap
from dbaide.desktop.theme import Theme

_COLUMN_WIDTH = 460


def _sync_wrapped_label_height(label: QLabel, width: int) -> None:
    """QLabel word-wrap needs an explicit width and minimum height or the last line clips."""
    label.setMinimumHeight(0)
    if width <= 0:
        return
    height = label.heightForWidth(width)
    if height > 0:
        label.setMinimumHeight(height)


class EmptyState(QWidget):
    action_clicked = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        body: str,
        actions: list[tuple[str, str]] | None = None,
        *,
        icon: str = "database",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._action_callbacks: dict[str, Callable[[], None]] = {}
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        column = QWidget()
        column.setFixedWidth(_COLUMN_WIDTH)
        col_layout = QVBoxLayout(column)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(8)
        col_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        if icon:
            tile = QLabel()
            tile.setFixedSize(44, 44)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setPixmap(svg_pixmap(icon, color=Theme.TEXT_2, size=26))
            tile.setStyleSheet(
                f"background: {Theme.PANEL_2}; border-radius: 8px;"
            )
            col_layout.addWidget(tile, alignment=Qt.AlignmentFlag.AlignHCenter)
            col_layout.addSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setWordWrap(True)
        self._title_label.setFixedWidth(_COLUMN_WIDTH)
        self._title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._title_label.setStyleSheet(
            f"color: {Theme.TEXT}; font-size:18px; font-weight:600; background: transparent;"
        )
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._body_label = QLabel(body)
        self._body_label.setWordWrap(True)
        self._body_label.setFixedWidth(_COLUMN_WIDTH)
        self._body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._body_label.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 13px; background: transparent;"
        )
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        col_layout.addWidget(self._title_label)
        col_layout.addWidget(self._body_label)

        if actions:
            row = QHBoxLayout()
            row.setContentsMargins(0, 8, 0, 0)
            row.setSpacing(8)
            row.addStretch(1)
            for index, (label, action_id) in enumerate(actions):
                btn = compact_button(label, primary=(index == 0), width=max(96, len(label) * 10 + 32))
                btn.clicked.connect(lambda _checked=False, aid=action_id: self.action_clicked.emit(aid))
                row.addWidget(btn)
            row.addStretch(1)
            col_layout.addLayout(row)

        layout.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._sync_heights()

    def _sync_heights(self) -> None:
        _sync_wrapped_label_height(self._title_label, _COLUMN_WIDTH)
        _sync_wrapped_label_height(self._body_label, _COLUMN_WIDTH)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._sync_heights()

    def set_text(self, title: str, body: str) -> None:
        self._title_label.setText(title)
        self._body_label.setText(body)
        self._sync_heights()
