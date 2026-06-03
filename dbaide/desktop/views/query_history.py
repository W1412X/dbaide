"""Query history panel — recall previously executed SQL (DBeaver-style).

Lists the most-recent statements run from the Workbench, newest first. Clicking a
row loads it back into the editor; double-clicking loads and runs it. The data
comes from :class:`dbaide.history.query_store.QueryHistoryStore`; this widget is
pure presentation — MainWindow feeds it ``load(entries)`` and owns the store.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.session_list import _relative_time
from dbaide.desktop.theme import Theme

_SQL_ROLE = Qt.ItemDataRole.UserRole


def _one_line(sql: str, limit: int = 120) -> str:
    text = " ".join((sql or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _HistoryRow(QWidget):
    def __init__(self, entry: dict[str, Any]) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)
        sql_label = QLabel(_one_line(entry.get("sql", "")))
        sql_label.setFont(QFont("Menlo", 11))
        ok = entry.get("ok", True)
        sql_label.setStyleSheet(
            f"color: {Theme.TEXT if ok else Theme.RED}; background: transparent;"
            f" font-size: 12px; line-height: 16px;"
        )
        lay.addWidget(sql_label)

        meta = self._meta_text(entry)
        if meta:
            meta_label = QLabel(meta)
            meta_label.setStyleSheet(
                f"color: {Theme.MUTED}; background: transparent; font-size: 10px;"
            )
            lay.addWidget(meta_label)

    @staticmethod
    def _meta_text(entry: dict[str, Any]) -> str:
        from dbaide.i18n import t
        bits: list[str] = []
        when = _relative_time(entry.get("ts") or 0)
        if when:
            bits.append(when)
        if not entry.get("ok", True):
            bits.append(t("history.failed"))
        else:
            rc = entry.get("row_count")
            if rc is not None:
                bits.append(t("history.rows", n=rc))
            ms = entry.get("elapsed_ms")
            if ms is not None:
                bits.append(f"{ms:.0f}ms")
        db = entry.get("database")
        if db:
            bits.append(str(db))
        return "  ·  ".join(bits)


class QueryHistoryPanel(QWidget):
    sql_selected = pyqtSignal(str)  # load into editor (single click)
    sql_run = pyqtSignal(str)       # load + run (double click)
    clear_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        title = QLabel(t("tab.history"))
        title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        bar.addWidget(title)
        bar.addStretch(1)
        self._clear_btn = compact_button(t("history.clear"), width=80)
        self._clear_btn.clicked.connect(self.clear_requested.emit)
        bar.addWidget(self._clear_btn)
        outer.addLayout(bar)

        self.stack = QStackedWidget()
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        hint = QLabel(t("history.empty_hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        el.addWidget(hint)
        el.addStretch(1)
        self.stack.addWidget(empty)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { border: none; background: transparent; }")
        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)
        self.stack.addWidget(self.list)
        outer.addWidget(self.stack, 1)

    def load(self, entries: list[dict[str, Any]]) -> None:
        self.list.clear()
        for entry in entries:
            item = QListWidgetItem(self.list)
            item.setData(_SQL_ROLE, entry.get("sql", ""))
            row = _HistoryRow(entry)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.stack.setCurrentIndex(1 if entries else 0)
        self._clear_btn.setEnabled(bool(entries))

    def _on_clicked(self, item: QListWidgetItem) -> None:
        sql = item.data(_SQL_ROLE)
        if sql:
            self.sql_selected.emit(str(sql))

    def _on_activated(self, item: QListWidgetItem) -> None:
        sql = item.data(_SQL_ROLE)
        if sql:
            self.sql_run.emit(str(sql))
