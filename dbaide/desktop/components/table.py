from __future__ import annotations

import json
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import AgentButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.theme import Theme
from dbaide.rendering.table import export_csv, export_insert, export_json, export_markdown_table


class ResultTableWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._columns: list[str] = []
        self._rows: list[dict[str, Any]] = []
        self._table_name = "table"  # used by "Copy as INSERT"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px;")
        toolbar.addWidget(self.meta)
        toolbar.addStretch(1)
        # Value-viewer toggle: a checkable button that reveals the inline panel
        # showing the selected cell's full value (with JSON pretty-printing).
        self.value_toggle = QToolButton()
        self.value_toggle.setCheckable(True)
        self.value_toggle.setIcon(svg_icon("panel-right", color=Theme.TEXT_2, size=15))
        self.value_toggle.setToolTip("Value viewer")
        self.value_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value_toggle.setFixedSize(30, 30)
        self.value_toggle.setStyleSheet(
            f"QToolButton {{ background: {Theme.PANEL_2}; border: none; border-radius: 7px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
            f"QToolButton:checked {{ background: {Theme.PANEL_3}; }}"
        )
        self.value_toggle.toggled.connect(self._toggle_value_viewer)
        toolbar.addWidget(self.value_toggle)
        self.export_menu = MenuButton("Export ▾", max_width=96)
        self.export_menu.add_action("Copy as CSV", self.copy_csv)
        self.export_menu.add_action("Copy as JSON", self.copy_json)
        self.export_menu.add_action("Copy as Markdown", self.copy_markdown)
        self.export_menu.add_action("Copy as INSERT", self.copy_insert)
        self.export_menu.add_separator()
        self.export_menu.add_action("Save as CSV…", self.save_csv)
        self.export_menu.add_action("Save as JSON…", self.save_json)
        toolbar.addWidget(self.export_menu)
        layout.addLayout(toolbar)
        self.table = QTableWidget()
        # Right-click a cell → copy the cell value or the whole row.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)
        self.table.setFont(QFont("Menlo", 10))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(72)
        self.table.horizontalHeader().setDefaultSectionSize(120)
        # Columns size to their content (capped) rather than letting the last column
        # balloon to fill the width — a numeric column stretched across half the grid
        # with its value pinned to the far right reads as broken. Trailing space on
        # the right is normal for a result grid.
        self.table.horizontalHeader().setStretchLastSection(False)
        # Row-number gutter (1-based, page-relative) like a database client.
        vh = self.table.verticalHeader()
        vh.setVisible(True)
        vh.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        # No striping, no full grid — horizontal row rules only (a faint line under
        # each row) with a quiet, underlined header. Reads clean like an AI-IDE grid.
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        # Long values are truncated for layout; double-click (or the tooltip) reveals
        # the full value.
        self.table.cellDoubleClicked.connect(self._show_full_cell)
        self.table.itemSelectionChanged.connect(self._update_value_viewer)
        # Header right-click → auto-fit columns (kept off the click/double-click
        # gestures so it never collides with the data browser's sort-on-click).
        hh = self.table.horizontalHeader()
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        # Ctrl/Cmd+C copies the current selection as TSV.
        from PyQt6.QtGui import QKeySequence, QShortcut
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        copy_sc.activated.connect(self._copy_selection)
        self.table.setStyleSheet(
            f"""
            QTableWidget {{
                background: {Theme.SURFACE};
                border: none;
                outline: none;
            }}
            QTableWidget::item {{
                border-bottom: 1px solid {Theme.BORDER_SOFT};
                padding: 4px 10px;
            }}
            QTableWidget::item:selected {{
                background: {Theme.PANEL_3};
                color: {Theme.TEXT};
            }}
            QHeaderView {{
                background: {Theme.SURFACE};
            }}
            QHeaderView::section:horizontal {{
                background: {Theme.SURFACE};
                color: {Theme.MUTED};
                padding: 7px 10px;
                border: none;
                border-bottom: 1px solid {Theme.BORDER};
                font-weight: 600;
            }}
            QHeaderView::section:vertical {{
                background: {Theme.SURFACE};
                color: {Theme.MUTED_2};
                padding: 0 8px;
                border: none;
                border-bottom: 1px solid {Theme.BORDER_SOFT};
                font-weight: 400;
            }}
            """
        )

        # Inline value viewer — hidden until toggled. Shows the selected cell's full
        # value, pretty-printing JSON. Sits under the grid in a resizable splitter.
        self._viewer = QWidget()
        vlay = QVBoxLayout(self._viewer)
        vlay.setContentsMargins(0, 6, 0, 0)
        vlay.setSpacing(4)
        vhead = QHBoxLayout()
        vhead.setContentsMargins(2, 0, 2, 0)
        self._viewer_label = QLabel("")
        self._viewer_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600;")
        vhead.addWidget(self._viewer_label)
        vhead.addStretch(1)
        self._viewer_copy = QToolButton()
        self._viewer_copy.setIcon(svg_icon("copy", color=Theme.TEXT_2, size=14))
        self._viewer_copy.setToolTip("Copy value")
        self._viewer_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._viewer_copy.setFixedSize(26, 26)
        self._viewer_copy.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
        )
        self._viewer_copy.clicked.connect(self._copy_value_viewer)
        vhead.addWidget(self._viewer_copy)
        vlay.addLayout(vhead)
        self._viewer_text = QPlainTextEdit()
        self._viewer_text.setReadOnly(True)
        self._viewer_text.setFont(QFont("Menlo", 11))
        self._viewer_text.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.CODE_BG}; border: 1px solid {Theme.BORDER_SOFT};"
            f" border-radius: 8px; }}"
        )
        vlay.addWidget(self._viewer_text, 1)

        self._split = QSplitter(Qt.Orientation.Vertical)
        self._split.addWidget(self.table)
        self._split.addWidget(self._viewer)
        self._split.setStretchFactor(0, 3)
        self._split.setStretchFactor(1, 1)
        self._viewer.setVisible(False)
        layout.addWidget(self._split)

    def _toggle_value_viewer(self, on: bool) -> None:
        self._viewer.setVisible(on)
        if on:
            if self._split.sizes()[1] == 0:
                h = max(200, self._split.height())
                self._split.setSizes([int(h * 0.7), int(h * 0.3)])
            self._update_value_viewer()

    def _current_cell(self) -> tuple[int, int] | None:
        items = self.table.selectedItems()
        if items:
            return items[0].row(), items[0].column()
        r, c = self.table.currentRow(), self.table.currentColumn()
        return (r, c) if r >= 0 and c >= 0 else None

    def _update_value_viewer(self) -> None:
        if not self._viewer.isVisible():
            return
        cell = self._current_cell()
        if cell is None:
            self._viewer_label.setText("")
            self._viewer_text.setPlainText("")
            return
        r, c = cell
        if not (0 <= r < len(self._rows) and 0 <= c < len(self._columns)):
            return
        col = self._columns[c]
        value = self._rows[r].get(col)
        self._viewer_label.setText(col)
        self._viewer_text.setPlainText(_pretty_value(value))

    def _copy_value_viewer(self) -> None:
        QApplication.clipboard().setText(self._viewer_text.toPlainText())

    def load(
        self,
        *,
        columns: list[str],
        rows: list[dict[str, Any]],
        row_count: int = 0,
        truncated: bool = False,
        elapsed_ms: float = 0.0,
        row_offset: int = 0,
    ) -> None:
        self._columns = columns or (list(rows[0].keys()) if rows else [])
        self._rows = rows or []
        self.table.clear()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels(self._columns)
        self.table.setRowCount(len(self._rows))
        # Row-number gutter — absolute (offset-aware) so pages read continuously.
        self.table.setVerticalHeaderLabels([str(row_offset + i + 1) for i in range(len(self._rows))])
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
        # Size every column to its content (capped), leaving any slack as trailing
        # space on the right — like a standard database-client grid. No single column
        # is stretched to fill, which previously left an awkward mid-grid gap when the
        # stretched column held short values.
        total = 0
        for i in range(self.table.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(i, min(420, max(72, header.sectionSize(i))))
            total += header.sectionSize(i)
        # If the content leaves real slack in the viewport, let the last column take
        # it so there's no thin dangling gap at the right edge. The fudge keeps us
        # just inside the viewport so no spurious horizontal scrollbar appears.
        slack = self.table.viewport().width() - total - 4
        if slack > 16 and self.table.columnCount():
            last = self.table.columnCount() - 1
            header.resizeSection(last, header.sectionSize(last) + slack)

    def _show_full_cell(self, row: int, col: int) -> None:
        if not (0 <= row < len(self._rows) and 0 <= col < len(self._columns)):
            return
        column = self._columns[col]
        value = self._rows[row].get(column)
        CellValueDialog(column, _full_text(value), parent=self).exec()

    def set_table_name(self, name: str) -> None:
        """Hint used by 'Copy as INSERT' (e.g. the browsed table's name)."""
        self._table_name = str(name or "table")

    def copy_csv(self) -> None:
        QApplication.clipboard().setText(export_csv(self._rows, self._columns))

    def copy_json(self) -> None:
        QApplication.clipboard().setText(export_json(self._rows, self._columns))

    def copy_markdown(self) -> None:
        QApplication.clipboard().setText(export_markdown_table(self._rows, self._columns))

    def copy_insert(self) -> None:
        QApplication.clipboard().setText(export_insert(self._rows, self._columns, table=self._table_name))

    def save_csv(self) -> None:
        self._save_to_file(export_csv(self._rows, self._columns), "csv", "CSV (*.csv)")

    def save_json(self) -> None:
        self._save_to_file(export_json(self._rows, self._columns), "json", "JSON (*.json)")

    def _save_to_file(self, content: str, ext: str, file_filter: str) -> None:
        from PyQt6.QtWidgets import QFileDialog
        suggested = f"{self._table_name}.{ext}"
        path, _ = QFileDialog.getSaveFileName(self, "Export results", suggested, file_filter)
        if path:
            self._write_file(path, content)

    @staticmethod
    def _write_file(path: str, content: str) -> bool:
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            return True
        except OSError:
            return False

    def _cell_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        r, c = item.row(), item.column()
        from dbaide.desktop.components.menu import _style_menu
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        _style_menu(menu)
        menu.addAction("Copy cell", lambda: QApplication.clipboard().setText(self._cell_text(r, c)))
        menu.addAction("Copy row (JSON)", lambda: QApplication.clipboard().setText(
            export_json([self._rows[r]], self._columns) if 0 <= r < len(self._rows) else ""))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _cell_text(self, row: int, col: int) -> str:
        if 0 <= row < len(self._rows) and 0 <= col < len(self._columns):
            return _full_text(self._rows[row].get(self._columns[col]))
        return ""

    def _header_menu(self, pos) -> None:
        from dbaide.desktop.components.menu import _style_menu
        from PyQt6.QtWidgets import QMenu
        hh = self.table.horizontalHeader()
        section = hh.logicalIndexAt(pos)
        menu = QMenu(self)
        _style_menu(menu)
        if section >= 0:
            menu.addAction("Auto-fit column",
                           lambda: self.table.resizeColumnToContents(section))
        menu.addAction("Auto-fit all columns", self.table.resizeColumnsToContents)
        menu.exec(hh.mapToGlobal(pos))

    def _copy_selection(self) -> None:
        """Copy the selected cells as TSV (single cell → just its value)."""
        items = self.table.selectedItems()
        if not items:
            return
        rows = sorted({it.row() for it in items})
        cols = sorted({it.column() for it in items})
        if len(rows) == 1 and len(cols) == 1:
            QApplication.clipboard().setText(self._cell_text(rows[0], cols[0]))
            return
        lines = []
        for r in rows:
            line = [self._cell_text(r, c) if self.table.item(r, c) and self.table.item(r, c).isSelected()
                    else "" for c in cols]
            lines.append("\t".join(line))
        QApplication.clipboard().setText("\n".join(lines))

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


def _pretty_value(value: Any) -> str:
    """Full cell text, pretty-printed when it's (or holds) JSON."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    text = _full_text(value)
    stripped = text.strip()
    if stripped[:1] in ("{", "[") and stripped[-1:] in ("}", "]"):
        try:
            return json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            pass
    return text


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
