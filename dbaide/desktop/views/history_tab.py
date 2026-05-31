from __future__ import annotations

import datetime
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from dbaide.desktop.theme import Theme


class HistoryTab(QWidget):
    """Workflow history list for the history popup."""

    history_selected = pyqtSignal(str)
    history_preview = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        hint = QLabel("Click to preview trace · double-click to open in Ask")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.itemClicked.connect(self._preview)
        self.list.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.list, 1)

    def load(self, entries: list[dict[str, Any]]) -> None:
        self.list.clear()
        if not entries:
            item = QListWidgetItem("No history yet. Ask a question to create the first workflow.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for entry in entries:
            q = str(entry.get("question") or "")[:72]
            status = str(entry.get("status") or "unknown")
            wid = str(entry.get("workflow_id") or "")
            created = _fmt_time(float(entry.get("created_at") or 0))
            item = QListWidgetItem(f"{created} · {status} · {q}")
            item.setData(Qt.ItemDataRole.UserRole, wid)
            item.setToolTip(f"{wid}\n{entry.get('question', '')}")
            if status == "failed":
                item.setForeground(_color(Theme.RED))
            elif status == "completed":
                item.setForeground(_color(Theme.GREEN))
            self.list.addItem(item)

    def _preview(self, item: QListWidgetItem) -> None:
        wid = item.data(Qt.ItemDataRole.UserRole)
        if wid:
            self.history_preview.emit(str(wid))

    def _open(self, item: QListWidgetItem) -> None:
        wid = item.data(Qt.ItemDataRole.UserRole)
        if wid:
            self.history_selected.emit(str(wid))


def _fmt_time(ts: float) -> str:
    if ts <= 0:
        return "--:--"
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _color(hex_color: str):
    from PyQt6.QtGui import QColor

    return QColor(hex_color)
