"""Header-row picker: shows a sheet's grid and lets the user click the header row.

Used from the Excel-collection staging dialog. The chosen anchors (per sheet, as
(header_row, start_col)) flow into ImportSpec.header_anchors; columns to the right and rows
below the chosen cell are matched automatically by the reader.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import Combo, dialog_action_row
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t as _pt

_MAX_ROWS = 40
_MAX_COLS = 26


class HeaderPreviewDialog(ChromeDialog):
    def __init__(self, parent, path: Path, current: dict[str, tuple[int, int]] | None = None,
                 *, logical_name: str = "", table_names: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        from dbaide.ingest import read_sheet_grids

        self._grids = read_sheet_grids(path)
        self._logical = logical_name or path.stem
        self._choice: dict[str, tuple[int, int]] = {}
        self._included: dict[str, bool] = {}
        self._table_names: dict[str, str] = dict(table_names or {})   # sheet → override (only if != default)
        for g in self._grids:
            self._choice[g.name] = (current or {}).get(g.name, (g.auto_header_row, g.auto_header_col))
            self._included[g.name] = True
        self._multi = len(self._grids) > 1

        self.setWindowTitle(_pt("excel.header_title"))
        self.setModal(True)
        self.setMinimumSize(620, 460)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(10)
        heading = QLabel(f"{_pt('excel.header_title')} · {path.name}")
        heading.setStyleSheet(f"color:{Theme.TEXT}; font-size:15px; font-weight:700; background:transparent;")
        root.addWidget(heading)
        hint = QLabel(_pt("excel.header_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        root.addWidget(hint)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._sheet_combo = Combo()
        self._sheet_combo.addItems([g.name for g in self._grids])
        self._sheet_combo.currentIndexChanged.connect(self._on_sheet)
        self._sheet_combo.setVisible(len(self._grids) > 1)
        sheet_label = QLabel(_pt("excel.header_sheet"))
        sheet_label.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; background:transparent;")
        sheet_label.setVisible(len(self._grids) > 1)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        self._include = QCheckBox(_pt("excel.header_include"))
        self._include.setVisible(self._multi)       # single-sheet files always import their one sheet
        self._include.toggled.connect(self._on_include_toggled)
        self._apply_all = compact_button(_pt("excel.header_apply_all"), width=132)
        self._apply_all.clicked.connect(self._apply_to_all_sheets)
        self._apply_all.setVisible(self._multi)
        top.addWidget(sheet_label)
        top.addWidget(self._sheet_combo)
        top.addWidget(self._include)
        top.addWidget(self._apply_all)
        top.addStretch(1)
        top.addWidget(self._status)
        root.addLayout(top)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel(_pt("excel.header_table_name"))
        name_label.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; background:transparent;")
        self._name_edit = QLineEdit()
        self._name_edit.setMaximumWidth(280)
        self._name_edit.textEdited.connect(self._on_table_name_edited)
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_edit)
        name_row.addStretch(1)
        root.addLayout(name_row)

        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.cellClicked.connect(self._on_cell)
        self._table.setStyleSheet("QTableWidget { background: transparent; }")
        root.addWidget(self._table, 1)

        actions_host, actions = dialog_action_row(top_margin=2)
        actions.addStretch(1)
        cancel = compact_button(_pt("dialog.cancel"), width=88)
        cancel.clicked.connect(self.reject)
        self._ok = compact_button(_pt("dialog.ok"), primary=True, width=88)
        self._ok.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(self._ok)
        root.addWidget(actions_host)

        if self._grids:
            self._render(0)

    # ── rendering ─────────────────────────────────────────────────────────────

    def _current(self):
        return self._grids[self._sheet_combo.currentIndex()] if self._grids else None

    def _on_sheet(self, index: int) -> None:
        if 0 <= index < len(self._grids):
            self._render(index)

    def _render(self, index: int) -> None:
        grid = self._grids[index]
        hr, hc = self._choice.get(grid.name, (grid.auto_header_row, grid.auto_header_col))
        # always keep the chosen/auto header cell within the previewed window
        rows = min(len(grid.cells), max(_MAX_ROWS, hr + 6))
        cols = min(max((len(r) for r in grid.cells), default=0), max(_MAX_COLS, hc + 6))
        self._table.clear()
        self._table.setRowCount(rows)
        self._table.setColumnCount(cols)
        self._table.setHorizontalHeaderLabels([str(c + 1) for c in range(cols)])
        for r in range(rows):
            for c in range(cols):
                row = grid.cells[r]
                val = row[c] if c < len(row) else None
                text = "" if val is None else str(val)
                if len(text) > 40:
                    text = text[:39] + "…"
                self._table.setItem(r, c, QTableWidgetItem(text))
        self._table.resizeColumnsToContents()
        self._include.blockSignals(True)
        self._include.setChecked(self._included.get(grid.name, True))
        self._include.blockSignals(False)
        self._name_edit.blockSignals(True)
        self._name_edit.setText(self._table_names.get(grid.name) or self._default_name(grid.name))
        self._name_edit.blockSignals(False)
        self._restyle()
        anchor = self._table.item(min(hr, rows - 1), min(hc, cols - 1)) if rows and cols else None
        if anchor is not None:
            self._table.scrollToItem(anchor)

    def _restyle(self) -> None:
        grid = self._current()
        if grid is None:
            return
        hr, hc = self._choice.get(grid.name, (grid.auto_header_row, grid.auto_header_col))
        excluded = not self._included.get(grid.name, True)
        header_bg = QBrush(QColor(Theme.ACCENT))
        header_fg = QBrush(QColor(Theme.ACCENT_TEXT))
        skipped_fg = QBrush(QColor(Theme.MUTED))
        normal_fg = QBrush(QColor(Theme.TEXT))
        clear_bg = QBrush(Qt.GlobalColor.transparent)
        for r in range(self._table.rowCount()):
            for c in range(self._table.columnCount()):
                item = self._table.item(r, c)
                if item is None:
                    continue
                if excluded:                            # whole sheet skipped → all muted
                    item.setBackground(clear_bg)
                    item.setForeground(skipped_fg)
                elif r == hr and c >= hc:               # the header span
                    item.setBackground(header_bg)
                    item.setForeground(header_fg)
                elif r < hr or c < hc:                  # above the header, or left of the table
                    item.setBackground(clear_bg)
                    item.setForeground(skipped_fg)
                else:                                   # data
                    item.setBackground(clear_bg)
                    item.setForeground(normal_fg)
        self._refresh_ok()

    def _set_status(self, text: str, *, bad: bool) -> None:
        color = Theme.RED if bad else Theme.MUTED
        self._status.setStyleSheet(f"color:{color}; font-size:12px; background:transparent;")
        self._status.setText(text)

    def _refresh_ok(self) -> None:
        included = [g for g in self._grids if self._included.get(g.name, True)]
        if not included:
            self._ok.setEnabled(False)
            self._set_status(_pt("excel.no_sheets_warn"), bad=True)
            return
        for g in included:                              # every imported sheet must be valid
            hr, hc = self._choice.get(g.name, (g.auto_header_row, g.auto_header_col))
            ok, warn = self._validate(g, hr, hc)
            if not ok:
                self._ok.setEnabled(False)
                self._set_status((f"{g.name}: {warn}" if self._multi else warn), bad=True)
                return
        self._ok.setEnabled(True)
        cur = self._current()
        if cur is not None and not self._included.get(cur.name, True):
            self._set_status(_pt("excel.sheet_excluded"), bad=False)
        elif cur is not None:
            hr, hc = self._choice.get(cur.name, (cur.auto_header_row, cur.auto_header_col))
            auto = " · " + _pt("excel.header_auto") if (hr, hc) == (cur.auto_header_row, cur.auto_header_col) else ""
            self._set_status(_pt("excel.header_current", r=hr + 1, c=hc + 1) + auto, bad=False)

    def _on_include_toggled(self, checked: bool) -> None:
        grid = self._current()
        if grid is not None:
            self._included[grid.name] = bool(checked)
            self._restyle()

    def _default_name(self, sheet: str) -> str:
        from dbaide.ingest import default_table_name
        return default_table_name(self._logical, sheet, single=len(self._grids) == 1)

    def _on_table_name_edited(self, text: str) -> None:
        grid = self._current()
        if grid is None:
            return
        text = text.strip()
        if text and text != self._default_name(grid.name):
            self._table_names[grid.name] = text     # only store genuine overrides
        else:
            self._table_names.pop(grid.name, None)

    @staticmethod
    def _validate(grid, hr: int, hc: int) -> tuple[bool, str]:
        """Reject anchors that would yield a garbage table: a header row with no label cell
        at/after the chosen column, or no data row beneath it."""
        rows = grid.cells

        def filled(r: int, c: int) -> bool:
            row = rows[r] if 0 <= r < len(rows) else []
            v = row[c] if 0 <= c < len(row) else None
            return v is not None and str(v).strip() != ""

        ncols = max((len(r) for r in rows), default=0)
        if not any(filled(hr, c) for c in range(hc, ncols)):
            return False, _pt("excel.header_empty_warn")
        has_data = any(filled(r, c) for r in range(hr + 1, len(rows)) for c in range(hc, ncols))
        if not has_data:
            return False, _pt("excel.header_no_data_warn")
        return True, ""

    def _on_cell(self, row: int, col: int) -> None:
        grid = self._current()
        if grid is not None:
            self._choice[grid.name] = (row, col)
            self._restyle()

    def _apply_to_all_sheets(self) -> None:
        grid = self._current()
        if grid is None:
            return
        anchor = self._choice.get(grid.name, (grid.auto_header_row, grid.auto_header_col))
        for g in self._grids:
            self._choice[g.name] = anchor
        self._restyle()

    def result_value(self) -> tuple[dict[str, tuple[int, int]], list[str], dict[str, str]]:
        included = [g.name for g in self._grids if self._included.get(g.name, True)]
        names = {k: v for k, v in self._table_names.items() if k in included}
        return dict(self._choice), included, names


def pick_header_rows(
    parent, path: Path, current: dict[str, tuple[int, int]] | None = None, *, logical_name: str = "",
) -> tuple[dict[str, tuple[int, int]], list[str], dict[str, str]] | None:
    """Returns ((sheet → (header_row, start_col)), included_sheets, (sheet → table name)) or None."""
    dialog = HeaderPreviewDialog(parent, path, current, logical_name=logical_name)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.result_value()
