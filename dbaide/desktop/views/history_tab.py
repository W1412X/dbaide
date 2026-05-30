from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from dbaide.desktop.components.base import SectionLabel
from dbaide.desktop.theme import Theme


class HistoryTab(QWidget):
    history_selected = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(SectionLabel("WORKFLOW HISTORY"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.list)

    def load(self, entries: list[dict[str, Any]]) -> None:
        self.list.clear()
        if not entries:
            self.list.addItem("No history yet. Ask a question to create the first workflow.")
            return
        for entry in entries:
            q = str(entry.get("question") or "")[:80]
            status = str(entry.get("status") or "")
            wid = str(entry.get("workflow_id") or "")
            item = QListWidgetItem(f"{wid} · {status} · {q}")
            item.setData(Qt.ItemDataRole.UserRole, wid)
            self.list.addItem(item)

    def _open(self, item: QListWidgetItem) -> None:
        wid = item.data(Qt.ItemDataRole.UserRole)
        if wid:
            self.history_selected.emit(str(wid))
