from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
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
        self._running_tools: dict[str, QTreeWidgetItem] = {}

    def load_events(self, events: list[dict[str, Any]]) -> None:
        self._live_root = None
        self._running_tools = {}
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
            item.addChild(detail)
            summary = str(event.get("summary") or "")
            if summary and summary != title:
                detail.addChild(QTreeWidgetItem(["", summary[:240], ""]))
            output = str(event.get("output_preview") or "")
            if output:
                detail.addChild(QTreeWidgetItem(["", output[:240], ""]))
            duration = event.get("duration_ms")
            if duration:
                detail.addChild(QTreeWidgetItem(["", f"{duration:.0f} ms", ""]))
            item.setExpanded(True)
            if status == "failed":
                for col in range(3):
                    item.setForeground(col, _red())

    def begin_live(self) -> None:
        self.clear()
        self._running_tools = {}
        self._live_root = QTreeWidgetItem(["", "Live workflow", "running"])
        self._live_root.setForeground(1, _blue())
        self.addTopLevelItem(self._live_root)
        self._live_root.setExpanded(True)

    def append_live(self, message: str) -> None:
        if not message.strip():
            return
        stage = "agent"
        title = message.strip()
        if message.startswith("[assets]"):
            stage = "build_assets"
            title = message.replace("[assets]", "", 1).strip()
        self.append_live_event(
            {"stage": stage, "title": title, "status": "running", "kind": "info"},
        )

    def append_live_event(self, event: dict[str, Any]) -> None:
        if self._live_root is None:
            self.begin_live()
        stage = str(event.get("stage") or "agent")
        title = str(event.get("title") or "").strip()
        status = str(event.get("status") or "running")
        kind = str(event.get("kind") or "")
        detail = str(event.get("detail") or event.get("summary") or "").strip()
        duration_ms = float(event.get("duration_ms") or 0)

        if status == "info" or kind == "substep":
            parent = self._running_tools.get(stage)
            if parent is not None:
                line = title or detail
                if line:
                    parent.addChild(QTreeWidgetItem(["", line[:400], ""]))
                    parent.setExpanded(True)
                return

        if status == "running" and stage not in {"loop", "agent", "decision", "build_assets"}:
            if stage in self._running_tools:
                old = self._running_tools.pop(stage)
                old.setText(2, "…")
                old.setForeground(2, _yellow())
            row = QTreeWidgetItem(["", stage, "running"])
            row.addChild(QTreeWidgetItem(["", title, ""]))
            if detail:
                row.addChild(QTreeWidgetItem(["", detail[:400], ""]))
            self._running_tools[stage] = row
            self._live_root.addChild(row)
            row.setForeground(1, _blue())
        elif status in {"completed", "failed", "waiting"} and stage in self._running_tools:
            row = self._running_tools.pop(stage)
            row.setText(2, status)
            row.setText(1, stage)
            if title and row.childCount() > 0:
                row.child(0).setText(1, title)
            if detail:
                row.addChild(QTreeWidgetItem(["", detail[:400], ""]))
            if duration_ms > 0:
                row.addChild(QTreeWidgetItem(["", f"{duration_ms:.0f} ms", ""]))
            color = _green() if status == "completed" else _yellow() if status == "waiting" else _red()
            row.setForeground(1, color)
            row.setForeground(2, color)
        else:
            row = QTreeWidgetItem(["", stage, status])
            if title:
                row.addChild(QTreeWidgetItem(["", title[:400], ""]))
            if detail:
                row.addChild(QTreeWidgetItem(["", detail[:400], ""]))
            if duration_ms > 0:
                row.addChild(QTreeWidgetItem(["", f"{duration_ms:.0f} ms", ""]))
            self._live_root.addChild(row)
            if status == "failed":
                row.setForeground(1, _red())

        self._live_root.setExpanded(True)
        self.scrollToItem(self._live_root)

    def end_live(self) -> None:
        for row in self._running_tools.values():
            row.setText(2, "…")
            row.setForeground(2, _yellow())
        self._running_tools.clear()
        if self._live_root is not None:
            self._live_root.setText(2, "done")
            self._live_root.setForeground(1, _green())
        self._live_root = None

    def clear_trace(self) -> None:
        self.clear()
        self._live_root = None
        self._running_tools = {}

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self.event_selected.emit(data)


def _fmt_time(ts: float) -> str:
    import datetime
    if ts <= 0:
        return "--:--:--"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _red() -> QColor:
    return QColor(Theme.RED)


def _blue() -> QColor:
    return QColor(Theme.BLUE)


def _green() -> QColor:
    return QColor(Theme.GREEN)


def _yellow() -> QColor:
    return QColor(Theme.YELLOW)
