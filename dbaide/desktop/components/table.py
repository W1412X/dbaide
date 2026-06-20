from __future__ import annotations

import json
import numbers as _numbers
import re
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.dialogs.file_dialogs import get_save_file_name
from dbaide.desktop.dialogs.message_dialog import warn as dialog_warn
from dbaide.desktop.theme import Theme
from dbaide.rendering.table import export_csv, export_insert, export_json, export_markdown_table


class ResultTableWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._columns: list[str] = []
        self._rows: list[dict[str, Any]] = []
        self._table_name = "table"  # used by "Copy as INSERT"
        self._dialect = "generic"
        self._cell_actions_provider = None  # optional (row, col) -> [(label, fn)]
        self._header_actions_provider = None  # optional (section) -> [(label, fn)]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        # Toolbar lives in its own widget so callers can hide it wholesale (e.g. the
        # Structure columns grid, which needs no value-viewer/export row).
        self._toolbar_widget = QWidget()
        toolbar = QHBoxLayout(self._toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px;")
        toolbar.addWidget(self.meta)
        toolbar.addStretch(1)
        # Double-clicking a cell opens its full value in a dialog, so the old inline
        # value-viewer toggle is gone — only the Export menu remains here.
        self.export_menu = MenuButton(
            t("result.export"),
            max_width=120,
            icon=svg_icon("download", color=Theme.TEXT_2, size=15),
            filled=True,
        )
        self.export_menu.add_action(t("result.copy_csv"), self.copy_csv)
        self.export_menu.add_action(t("result.copy_json"), self.copy_json)
        self.export_menu.add_action(t("result.copy_markdown"), self.copy_markdown)
        self.export_menu.add_action(t("result.copy_insert"), self.copy_insert)
        self.export_menu.add_separator()
        self.export_menu.add_action(t("result.save_csv"), self.save_csv)
        self.export_menu.add_action(t("result.save_json"), self.save_json)
        toolbar.addWidget(self.export_menu)
        layout.addWidget(self._toolbar_widget)
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
            /* Top-left corner (row-gutter × column-header intersection). Unstyled it
               falls back to a light native-grey box that clashes with the dark header. */
            QTableCornerButton::section {{
                background: {Theme.SURFACE};
                border: none;
                border-bottom: 1px solid {Theme.BORDER};
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
        # A column is numeric iff every non-null value is numeric AND at least one is.
        # Single pass per cell (the old any()+all() evaluated _is_numeric up to twice
        # per cell on every page load); each cell's _is_numeric is computed once here.
        numeric_cols: set[int] = set()
        for c_idx, col in enumerate(self._columns):
            saw_numeric = False
            all_ok = True
            for row in self._rows:
                value = row.get(col)
                if value is None:
                    continue
                if _is_numeric(value):
                    saw_numeric = True
                else:
                    all_ok = False
                    break
            if saw_numeric and all_ok:
                numeric_cols.add(c_idx)
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
        from dbaide.i18n import t as _t
        total = row_count or len(self._rows)
        suffix = _t("result.truncated_suffix") if truncated else ""
        elapsed = f" · {elapsed_ms:.0f}ms" if elapsed_ms else ""
        self.meta.setText(_t("result.showing", shown=len(self._rows), total=total,
                             suffix=suffix, elapsed=elapsed))

    def _fit_columns(self) -> None:
        """Snug, content-sized columns. The first text column gets a Stretch resize
        mode so it absorbs any slack (and re-absorbs it on resize), keeping numeric
        columns snug instead of ballooning one to fill the grid."""
        if not self._columns:
            return
        header = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        # Size every column to its content (capped), then let the last column absorb
        # any remaining width. setStretchLastSection handles both the underfill case
        # (last column grows to fill — no dangling edge gap) and resize, and never
        # overflows into a spurious horizontal scrollbar. Standard DB-client grid feel.
        header.setStretchLastSection(False)
        for i in range(self.table.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(i, min(420, max(72, header.sectionSize(i))))
        header.setStretchLastSection(True)

    def _show_full_cell(self, row: int, col: int) -> None:
        if not (0 <= row < len(self._rows) and 0 <= col < len(self._columns)):
            return
        column = self._columns[col]
        value = self._rows[row].get(column)
        # Pretty-print JSON values (the popup replaces the old inline value viewer).
        CellValueDialog(column, _pretty_value(value), parent=self).exec()

    def set_table_name(self, name: str, *, dialect: str = "") -> None:
        """Hint used by 'Copy as INSERT' (e.g. the browsed table's name)."""
        self._table_name = str(name or "table")
        if dialect:
            self._dialect = dialect

    def copy_csv(self) -> None:
        QApplication.clipboard().setText(export_csv(self._rows, self._columns))

    def copy_json(self) -> None:
        QApplication.clipboard().setText(export_json(self._rows, self._columns))

    def copy_markdown(self) -> None:
        QApplication.clipboard().setText(export_markdown_table(self._rows, self._columns))

    def copy_insert(self) -> None:
        QApplication.clipboard().setText(export_insert(self._rows, self._columns, table=self._table_name, dialect=self._dialect))

    def save_csv(self) -> None:
        self._save_to_file(export_csv(self._rows, self._columns), "csv", "CSV (*.csv)")

    def save_json(self) -> None:
        self._save_to_file(export_json(self._rows, self._columns), "json", "JSON (*.json)")

    def _save_to_file(self, content: str, ext: str, file_filter: str) -> None:
        suggested = f"{self._table_name}.{ext}"
        path, _ = get_save_file_name(self, self._t("result.export_title"), suggested, file_filter)
        if not path:
            return
        # Surface write failures (permission denied, disk full, bad path) — otherwise
        # the user believes the export succeeded when no file was written.
        if not self._write_file(path, content):
            dialog_warn(self, self._t("result.export_title"), self._t("result.export_failed", path=path))

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
        menu.addAction(self._t("result.copy_cell"), lambda: QApplication.clipboard().setText(self._cell_text(r, c)))
        menu.addAction(self._t("result.copy_row"), lambda: QApplication.clipboard().setText(
            export_json([self._rows[r]], self._columns) if 0 <= r < len(self._rows) else ""))
        # Context-specific extras (e.g. the data browser's "Open referenced row").
        if self._cell_actions_provider is not None:
            extras = self._cell_actions_provider(r, c) or []
            if extras:
                menu.addSeparator()
                for label, fn in extras:
                    menu.addAction(label, fn)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def set_cell_actions_provider(self, provider) -> None:
        """Provide extra right-click actions: (row, col) -> [(label, callable)]."""
        self._cell_actions_provider = provider

    def set_toolbar_visible(self, visible: bool) -> None:
        """Show/hide the value-viewer + export toolbar row (hidden for read-only
        schema grids that don't need it)."""
        self._toolbar_widget.setVisible(visible)

    def _cell_text(self, row: int, col: int) -> str:
        if 0 <= row < len(self._rows) and 0 <= col < len(self._columns):
            return _full_text(self._rows[row].get(self._columns[col]))
        return ""

    def set_header_actions_provider(self, provider) -> None:
        """Provide extra header right-click actions: (section) -> [(label, callable)].
        Used by the data browser to offer ascending/descending sort on a column."""
        self._header_actions_provider = provider

    def _header_menu(self, pos) -> None:
        from dbaide.desktop.components.menu import _style_menu
        from PyQt6.QtWidgets import QMenu
        hh = self.table.horizontalHeader()
        section = hh.logicalIndexAt(pos)
        menu = QMenu(self)
        _style_menu(menu)
        # Context-specific actions first (e.g. the data browser's sort options).
        if section >= 0 and self._header_actions_provider is not None:
            for label, fn in (self._header_actions_provider(section) or []):
                menu.addAction(label, fn)
            menu.addSeparator()
        if section >= 0:
            menu.addAction(self._t("result.autofit_column"),
                           lambda: self.table.resizeColumnToContents(section))
        menu.addAction(self._t("result.autofit_all"), self.table.resizeColumnsToContents)
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
        from dbaide.i18n import t as _t
        self.meta.setText(_t("result.no_results"))


from dbaide.desktop.theme import Theme
from dbaide.desktop.window_chrome import ChromeDialog


class CellValueDialog(ChromeDialog):
    """Shows a single cell's full, untruncated value with a copy action."""

    def __init__(self, column: str, value: str, *, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t as _t
        self.setWindowTitle(column or _t("result.value_title"))
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
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        copy_btn = compact_button(_t("btn.copy"), icon=svg_icon("copy", color=Theme.TEXT_2, size=14), width=78)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(value))
        buttons.addWidget(copy_btn)
        close_btn = compact_button(_t("btn.close"), icon=svg_icon("x", color=Theme.TEXT_2, size=14), width=82)
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)


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
    # The grid is single-line: show multi-line / tabbed values as a clean one-line
    # preview (newlines → ↵, tabs → space) so the row doesn't render only its first
    # line or with stray control glyphs. The full value is on double-click.
    if "\n" in text or "\r" in text or "\t" in text:
        text = text.replace("\r\n", "↵").replace("\n", "↵").replace("\r", "↵").replace("\t", " ")
    return text[:120] + "…" if len(text) > 120 else text


# A real numeric string: optional sign, digits with optional decimal/exponent. Does
# NOT match "inf"/"nan"/"1_000" — float() accepts those, but a text column holding
# them should align left, not be mistaken for a numeric column.
_NUMERIC_STR_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _is_numeric(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, _numbers.Number):  # int / float / Decimal from the driver
        return True
    return bool(_NUMERIC_STR_RE.match(str(value).strip()))
