from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
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
        # Columns size to their content (capped) rather than letting the last column
        # balloon to fill the width — a numeric column stretched across half the grid
        # with its value pinned to the far right reads as broken. Trailing space on
        # the right is normal for a result grid.
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        # Long values are truncated for layout; double-click (or the tooltip) reveals
        # the full value.
        self.table.cellDoubleClicked.connect(self._show_full_cell)
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
        # Unified alignment: every cell is vertically centred; numbers align right,
        # everything else left. Headers follow their column so they line up.
        numeric_cols = {
            c_idx for c_idx, col in enumerate(self._columns)
            if any(_is_numeric(row.get(col)) for row in self._rows)
            and all(row.get(col) is None or _is_numeric(row.get(col)) for row in self._rows)
        }
        for c_idx in range(len(self._columns)):
            header_item = self.table.horizontalHeaderItem(c_idx)
            if header_item is None:
                continue
            # Header alignment follows the column's data: numbers right, text left
            # (Qt centres header text by default, which misaligned text columns).
            horizontal = (Qt.AlignmentFlag.AlignRight if c_idx in numeric_cols
                          else Qt.AlignmentFlag.AlignLeft)
            header_item.setTextAlignment(int(horizontal | Qt.AlignmentFlag.AlignVCenter))
        for r_idx, row in enumerate(self._rows):
            for c_idx, col in enumerate(self._columns):
                value = row.get(col)
                item = QTableWidgetItem(_format_cell(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                horizontal = Qt.AlignmentFlag.AlignRight if c_idx in numeric_cols else Qt.AlignmentFlag.AlignLeft
                item.setTextAlignment(int(horizontal | Qt.AlignmentFlag.AlignVCenter))
                if value is None:
                    item.setForeground(QColor(Theme.NULL))
                else:
                    item.setToolTip(_full_text(value))  # full value on hover
                self.table.setItem(r_idx, c_idx, item)
        self._fit_columns()
        total = row_count or len(self._rows)
        suffix = " · truncated" if truncated else ""
        elapsed = f" · {elapsed_ms:.0f}ms" if elapsed_ms else ""
        self.meta.setText(f"Showing {len(self._rows)} of {total} rows{suffix}{elapsed}")

    def _fit_columns(self) -> None:
        """Snug, content-sized columns. The first text column gets a Stretch resize
        mode so it absorbs any slack (and re-absorbs it on resize), keeping numeric
        columns snug instead of ballooning one to fill the grid."""
        if not self._columns:
            return
        header = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        # First column whose values are not numeric — the natural one to widen.
        text_col = next(
            (i for i, c in enumerate(self._columns)
             if not any(_is_numeric(r.get(c)) for r in self._rows)),
            0,
        )
        for i in range(self.table.columnCount()):
            if i == text_col:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(i, min(420, max(72, header.sectionSize(i))))

    def _show_full_cell(self, row: int, col: int) -> None:
        if not (0 <= row < len(self._rows) and 0 <= col < len(self._columns)):
            return
        column = self._columns[col]
        value = self._rows[row].get(column)
        CellValueDialog(column, _full_text(value), parent=self).exec()

    def copy_csv(self) -> None:
        QApplication.clipboard().setText(export_csv(self._rows, self._columns))

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.meta.setText("No results")


class CellValueDialog(QDialog):
    """Shows a single cell's full, untruncated value with a copy action."""

    def __init__(self, column: str, value: str, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(column or "Value")
        self.resize(560, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("Menlo", 11))
        view.setPlainText(value)
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox()
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(value))
        buttons.addButton(copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _full_text(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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
