from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from dbaide.desktop.theme import Theme


class TracePanel(QTreeWidget):
    event_selected = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["Time", "Stage", "Status"])
        self.header().setStretchLastSection(True)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setFont(QFont("Menlo", 10))
        self.itemClicked.connect(self._on_click)
        self._live_root: QTreeWidgetItem | None = None

    def load_events(self, events: list[dict[str, Any]]) -> None:
        self._live_root = None
        self.clear()
        for event in events:
            ts = event.get("timestamp") or 0
            time_text = _fmt_time(ts) if ts else "--:--:--"
            stage = str(event.get("stage") or "")
            status = str(event.get("status") or "")
            title = str(event.get("title") or "")
            item = QTreeWidgetItem([time_text, stage, status])
            item.setData(0, Qt.ItemDataRole.UserRole, event)
            self.addTopLevelItem(item)
            detail = QTreeWidgetItem(["", title, ""])
            summary = str(event.get("summary") or "")
            if summary:
                detail.addChild(QTreeWidgetItem(["", summary[:200], ""]))
            output = str(event.get("output_preview") or "")
            if output:
                detail.addChild(QTreeWidgetItem(["", output[:200], ""]))
            duration = event.get("duration_ms")
            if duration:
                detail.addChild(QTreeWidgetItem(["", f"{duration:.0f} ms", ""]))
            item.addChild(detail)
            item.setExpanded(True)
            if status == "failed":
                for col in range(3):
                    item.setForeground(col, _red())

    def begin_live(self) -> None:
        self.clear()
        self._live_root = QTreeWidgetItem(["", "Agent activity", "running"])
        self._live_root.setForeground(1, _blue())
        self.addTopLevelItem(self._live_root)
        self._live_root.setExpanded(True)

    def append_live(self, message: str) -> None:
        if not message.strip():
            return
        if self._live_root is None:
            self.begin_live()
        item = QTreeWidgetItem(["", message.strip()[:240], ""])
        self._live_root.addChild(item)
        self._live_root.setExpanded(True)

    def end_live(self) -> None:
        self._live_root = None

    def clear_trace(self) -> None:
        self.clear()
        self._live_root = None

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self.event_selected.emit(data)


def _fmt_time(ts: float) -> str:
    import datetime
    if ts <= 0:
        return "--:--:--"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _red():
    from PyQt6.QtGui import QColor
    return QColor(Theme.RED)


def _blue():
    from PyQt6.QtGui import QColor
    return QColor(Theme.BLUE)
