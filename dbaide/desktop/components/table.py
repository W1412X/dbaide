from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import AgentButton
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.theme import Theme
from dbaide.rendering.table import export_csv


class ResultTableWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._columns: list[str] = []
        self._rows: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px;")
        toolbar.addWidget(self.meta)
        toolbar.addStretch(1)
        self.export_menu = MenuButton("Export ▾", max_width=96)
        self.export_menu.add_action("Copy as CSV", self.copy_csv)
        toolbar.addWidget(self.export_menu)
        layout.addLayout(toolbar)
        self.table = QTableWidget()
        self.table.setFont(QFont("Menlo", 10))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(72)
        self.table.horizontalHeader().setDefaultSectionSize(120)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            f"""
            QTableWidget {{
                alternate-background-color: {Theme.PANEL};
                gridline-color: {Theme.BORDER_SOFT};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 8px;
            }}
            QHeaderView::section {{
                background: {Theme.PANEL_2};
                color: {Theme.TEXT_2};
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {Theme.BORDER_SOFT};
            }}
            """
        )
        layout.addWidget(self.table)

    def load(
        self,
        *,
        columns: list[str],
        rows: list[dict[str, Any]],
        row_count: int = 0,
        truncated: bool = False,
        elapsed_ms: float = 0.0,
    ) -> None:
        self._columns = columns or (list(rows[0].keys()) if rows else [])
        self._rows = rows or []
        self.table.clear()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels(self._columns)
        self.table.setRowCount(len(self._rows))
        for r_idx, row in enumerate(self._rows):
            for c_idx, col in enumerate(self._columns):
                item = QTableWidgetItem(_format_cell(row.get(col)))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if _is_numeric(row.get(col)):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if row.get(col) is None:
                    item.setForeground(QColor(Theme.NULL))
                self.table.setItem(r_idx, c_idx, item)
        total = row_count or len(self._rows)
        suffix = " · truncated" if truncated else ""
        elapsed = f" · {elapsed_ms:.0f}ms" if elapsed_ms else ""
        self.meta.setText(f"Showing {len(self._rows)} of {total} rows{suffix}{elapsed}")

    def copy_csv(self) -> None:
        QApplication.clipboard().setText(export_csv(self._rows, self._columns))

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.meta.setText("No results")


def _format_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value == "":
        return '""'
    text = str(value)
    return text[:120] + "…" if len(text) > 120 else text


def _is_numeric(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        float(str(value))
        return True
    except ValueError:
        return False
