"""Read-only preview of an imported Excel/CSV collection — pick a table, see its first rows.

Confirms what the import produced (table names, columns, sample data) without leaving Settings.
The data is just a local read-only SQLite file, so this queries it directly.
"""

from __future__ import annotations

import sqlite3

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import Combo
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t as _pt

_SAMPLE_LIMIT = 50


class CollectionPreviewDialog(ChromeDialog):
    def __init__(self, parent, collection, *, name: str = "") -> None:
        super().__init__(parent)
        # [(display_name, table, [column names], row_count), …] across all workbooks/sheets
        self._tables = [
            (sheet.display_name, sheet.table, [c.name for c in sheet.columns], sheet.row_count)
            for wb in collection.workbooks() for sheet in wb.sheets
        ]
        self._db_path = collection.db_path

        self.setWindowTitle(_pt("excel.preview_title", name=name))
        self.setModal(True)
        self.setMinimumSize(680, 480)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(10)
        heading = QLabel(_pt("excel.preview_title", name=name))
        heading.setStyleSheet(f"color:{Theme.TEXT}; font-size:15px; font-weight:700; background:transparent;")
        root.addWidget(heading)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._combo = Combo()
        self._combo.addItems([t[0] for t in self._tables])
        self._combo.currentIndexChanged.connect(self._render)
        self._combo.setVisible(len(self._tables) > 1)
        self._meta = QLabel("")
        self._meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        top.addWidget(self._combo)
        top.addStretch(1)
        top.addWidget(self._meta)
        root.addLayout(top)

        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setStyleSheet("QTableWidget { background: transparent; }")
        root.addWidget(self._table, 1)

        close = compact_button(_pt("dialog.ok"), width=88)
        close.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)

        if self._tables:
            self._render(0)
        else:
            self._meta.setText(_pt("excel.preview_empty"))

    def _render(self, index: int) -> None:
        if not (0 <= index < len(self._tables)):
            return
        display, table, columns, row_count = self._tables[index]
        rows: list[tuple] = []
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                cur = con.execute(f'SELECT * FROM {_q(table)} LIMIT {_SAMPLE_LIMIT}')
                rows = cur.fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            rows = []
        self._table.clear()
        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(len(columns)):
                val = row[c] if c < len(row) else None
                self._table.setItem(r, c, QTableWidgetItem("" if val is None else str(val)))
        self._table.resizeColumnsToContents()
        self._meta.setText(_pt("excel.preview_rows", rows=f"{row_count:,}", cols=len(columns), limit=_SAMPLE_LIMIT))


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'
