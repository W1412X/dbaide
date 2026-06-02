"""Chat session list — the navigable list of conversation threads (会话).

A header with a "New chat" action over a list of sessions; each row shows the
session title and a muted subtitle (turn count · relative time). Right-click a row
to rename or delete it.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import SectionLabel
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import plus_icon
from dbaide.desktop.theme import Theme

_ID_ROLE = Qt.ItemDataRole.UserRole


def _relative_time(ts: float) -> str:
    if not ts:
        return ""
    delta = max(0.0, time.time() - float(ts))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)}d ago"
    return time.strftime("%b %d", time.localtime(ts))


class _SessionRow(QWidget):
    """Two-line row: title over a muted 'N turns · time' subtitle."""

    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        self._title = QLabel(title)
        self._title.setFont(QFont("Inter", 12, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        sub = QLabel(subtitle)
        sub.setFont(QFont("Inter", 10))
        sub.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
        layout.addWidget(self._title)
        layout.addWidget(sub)


class SessionList(QWidget):
    new_requested = pyqtSignal()
    selected = pyqtSignal(str)             # session_id
    rename_requested = pyqtSignal(str, str)  # session_id, new_title
    delete_requested = pyqtSignal(str)       # session_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(SectionLabel("CHATS"))
        header.addStretch(1)
        self._new_btn = IconToolButton(plus_icon(), "New chat")
        self._new_btn.clicked.connect(self.new_requested.emit)
        header.addWidget(self._new_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._on_click)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        layout.addWidget(self.list, 1)

        self._current = ""
        self._empty: QListWidgetItem | None = None

    def load(self, sessions: list[dict[str, Any]]) -> None:
        self.list.clear()
        if not sessions:
            item = QListWidgetItem("No chats yet — ask a question to start one.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(self.palette().color(self.foregroundRole()))
            from PyQt6.QtGui import QColor
            item.setForeground(QColor(Theme.MUTED))
            self.list.addItem(item)
            return
        for s in sessions:
            sid = str(s.get("session_id") or "")
            title = str(s.get("title") or "New chat")
            n = int(s.get("turn_count") or 0)
            when = _relative_time(float(s.get("updated_at") or s.get("created_at") or 0))
            bits = [f"{n} turn{'s' if n != 1 else ''}"]
            if when:
                bits.append(when)
            item = QListWidgetItem()
            item.setData(_ID_ROLE, sid)
            item.setSizeHint(_SessionRow(title, " · ".join(bits)).sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, _SessionRow(title, " · ".join(bits)))
        self.set_current(self._current)

    def set_current(self, session_id: str) -> None:
        self._current = str(session_id or "")
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is not None and item.data(_ID_ROLE) == self._current and self._current:
                self.list.setCurrentItem(item)
                return
        self.list.clearSelection()

    def _on_click(self, item: QListWidgetItem) -> None:
        sid = item.data(_ID_ROLE)
        if sid:
            self._current = str(sid)
            self.selected.emit(str(sid))

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or not item.data(_ID_ROLE):
            return
        sid = str(item.data(_ID_ROLE))
        menu = QMenu(self)
        from dbaide.desktop.components.menu import _style_menu
        _style_menu(menu)
        rename = QAction("Rename…", menu)
        rename.triggered.connect(lambda: self._rename(sid))
        delete = QAction("Delete", menu)
        delete.triggered.connect(lambda: self.delete_requested.emit(sid))
        menu.addAction(rename)
        menu.addAction(delete)
        menu.exec(self.list.mapToGlobal(pos))

    def _rename(self, session_id: str) -> None:
        current = ""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is not None and it.data(_ID_ROLE) == session_id:
                w = self.list.itemWidget(it)
                current = w._title.text() if isinstance(w, _SessionRow) else ""
                break
        title, ok = QInputDialog.getText(self, "Rename chat", "Title:", text=current)
        if ok and title.strip():
            self.rename_requested.emit(session_id, title.strip())
