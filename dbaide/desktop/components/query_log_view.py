"""Live SQL audit view: every statement the system runs (agent / build / SQL tab /
join validation / EXPLAIN / profiling), with caller, rows, elapsed and full text.

This is the universal "is every auto-executed SQL traceable?" surface — it reads
from the per-instance QueryLog, so it covers *all* callers, not just the agent.
"""

from __future__ import annotations

import datetime

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHeaderView,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.theme import Theme

_ROLE = Qt.ItemDataRole.UserRole
_CAP = 500  # keep the on-screen list bounded

# caller → accent colour, so build vs agent vs gui is readable at a glance.
_CALLER_COLOR = {
    "agent": Theme.BLUE,
    "build": Theme.GREEN,
    "gui": Theme.YELLOW,
    "cli": Theme.YELLOW,
}


class QueryLogView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        split = QSplitter(Qt.Orientation.Vertical)
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Time", "Caller", "Rows", "SQL"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setRootIsDecorated(False)
        self._tree.setFont(QFont("Menlo", 10))
        self._tree.itemClicked.connect(self._on_click)

        self._detail = QTextBrowser()
        self._detail.setFont(QFont("Menlo", 11))
        configure_readonly_text_view(self._detail)

        split.addWidget(self._tree)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split)
        self._set_placeholder()

    def _set_placeholder(self) -> None:
        from dbaide.i18n import t
        self._detail.setPlaceholderText(t("queries.empty"))

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, entries: list[dict]) -> None:
        self._tree.clear()
        for entry in entries[-_CAP:]:
            self._add(entry, scroll=False)
        self._scroll_bottom()

    def append(self, entry: dict) -> None:
        self._add(entry, scroll=True)
        while self._tree.topLevelItemCount() > _CAP:
            self._tree.takeTopLevelItem(0)

    def clear(self) -> None:
        self._tree.clear()
        self._detail.clear()

    # ── Internals ──────────────────────────────────────────────────────────────

    def _add(self, entry: dict, *, scroll: bool) -> None:
        ts = entry.get("ts") or 0
        time_text = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--:--:--"
        caller = str(entry.get("caller") or "")
        rows = entry.get("row_count")
        sql = " ".join(str(entry.get("sql") or "").split())
        status = str(entry.get("status") or "ok")
        item = QTreeWidgetItem([time_text, caller, "" if rows is None else str(rows), sql])
        item.setForeground(0, QColor(Theme.MUTED))
        item.setForeground(1, QColor(_CALLER_COLOR.get(caller, Theme.TEXT_2)))
        item.setForeground(3, QColor(Theme.RED if status != "ok" else Theme.TEXT))
        item.setData(0, _ROLE, entry)
        self._tree.addTopLevelItem(item)
        if scroll:
            self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        n = self._tree.topLevelItemCount()
        if n:
            self._tree.scrollToItem(self._tree.topLevelItem(n - 1))

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        entry = item.data(0, _ROLE)
        if not isinstance(entry, dict):
            return
        self._detail.setPlainText(_format(entry))


class QueryLogBridge(QObject):
    """Subscribes to a per-instance QueryLog and re-emits entries as a Qt signal.

    Queries run on worker threads (agent/build), so the subscriber fires off the GUI
    thread; emitting a signal marshals each entry safely back to the GUI thread.
    """

    entry = pyqtSignal(dict)

    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._unsub = None
        self._instance = ""

    def watch(self, instance: str) -> None:
        if instance == self._instance:
            return
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None
        self._instance = instance
        if instance:
            try:
                self._unsub = self._service.subscribe_queries(
                    instance, lambda e: self.entry.emit(e.to_dict())
                )
            except Exception:
                self._unsub = None


def _format(entry: dict) -> str:
    ts = entry.get("ts") or 0
    when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "?"
    meta = [f"caller: {entry.get('caller', '?')}", f"when: {when}",
            f"rows: {entry.get('row_count', '?')}", f"{float(entry.get('elapsed_ms') or 0):.1f} ms",
            f"status: {entry.get('status', 'ok')}"]
    if entry.get("database"):
        meta.insert(1, f"db: {entry['database']}")
    lines = [" · ".join(meta), ""]
    if entry.get("error"):
        lines.append(f"error: {entry['error']}")
        lines.append("")
    lines.append(str(entry.get("sql") or ""))
    return "\n".join(lines)
