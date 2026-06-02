"""Question-memory popup — view and prune the worked examples the agent has
distilled from effective past questions (the memory mechanism)."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.menu import _style_menu
from dbaide.desktop.theme import Theme
from dbaide.i18n import t

_ID_ROLE = Qt.ItemDataRole.UserRole


class _MemoryRow(QWidget):
    """Question over its worked SQL (muted, monospace, elided)."""

    def __init__(self, question: str, sql: str, database: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(f"{question}\n\n{sql}")  # full text on hover (the SQL line elides)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(2)
        q = QLabel(question)
        q.setFont(QFont("Inter", 12, QFont.Weight.DemiBold))
        q.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        q.setTextFormat(Qt.TextFormat.PlainText)
        q.setSizePolicy(q.sizePolicy().horizontalPolicy(), q.sizePolicy().verticalPolicy())
        suffix = f"   ·   {database}" if database else ""
        s = QLabel(sql + suffix)
        s.setFont(QFont("Menlo", 10))
        s.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
        s.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(q)
        layout.addWidget(s)


class MemoryDialog(QDialog):
    delete_requested = pyqtSignal(str)  # item id
    clear_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("memory.title"))
        self.setModal(False)
        self.resize(620, 560)
        self.setMinimumSize(420, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        hint = QLabel(t("memory.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.list.setWordWrap(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        layout.addWidget(self.list, 1)

        footer = QHBoxLayout()
        self._clear_btn = compact_button(t("memory.clear_all"), width=120)
        self._clear_btn.clicked.connect(self.clear_requested.emit)
        footer.addWidget(self._clear_btn)
        footer.addStretch(1)
        layout.addLayout(footer)

    def load(self, items: list[dict[str, Any]]) -> None:
        self.list.clear()
        self._clear_btn.setEnabled(bool(items))
        if not items:
            empty = QListWidgetItem(t("memory.empty"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            from PyQt6.QtGui import QColor
            empty.setForeground(QColor(Theme.MUTED))
            self.list.addItem(empty)
            return
        for it in items:
            row = _MemoryRow(
                str(it.get("question") or ""),
                " ".join(str(it.get("sql") or "").split()),
                str(it.get("database") or ""),
            )
            item = QListWidgetItem()
            item.setData(_ID_ROLE, str(it.get("id") or ""))
            hint = row.sizeHint()
            hint.setHeight(hint.height() + 4)
            item.setSizeHint(hint)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or not item.data(_ID_ROLE):
            return
        menu = QMenu(self)
        _style_menu(menu)
        delete = QAction(t("memory.delete"), menu)
        delete.triggered.connect(lambda: self.delete_requested.emit(str(item.data(_ID_ROLE))))
        menu.addAction(delete)
        menu.exec(self.list.mapToGlobal(pos))
