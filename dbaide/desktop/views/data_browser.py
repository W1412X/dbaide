"""Table data browser — a read-only, paginated, sortable grid for one table.

Double-clicking a table in the schema tree opens it here. The widget owns the
browse *state* (table, page, sort, filter) and emits ``query_requested`` whenever
that state changes; MainWindow runs it as a ``browse_table`` one-off and feeds the
result back via ``show_result``. Pagination is cursor-style (LIMIT/OFFSET, "more =
a full page returned") so it stays cheap on large tables — no COUNT(*).
"""
from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import QSize, Qt, QStringListModel, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.menu import PillSelect
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme

_PAGE_SIZES = ("50", "100", "200", "500")


class DataBrowser(QWidget):
    query_requested = pyqtSignal(dict)  # browse_table payload
    count_requested = pyqtSignal(dict)  # count_table payload (on-demand exact total)
    navigate_fk = pyqtSignal(str, str, object)  # (ref_table, ref_column, value)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._conn = ""
        self._db = ""
        self._table = ""
        self._page_size = 100
        self._offset = 0
        self._order_by = ""
        self._order_dir = "asc"
        self._where = ""
        self._total: int | None = None      # exact COUNT(*), once requested
        self._total_where = None             # the WHERE the total was computed for
        self._fk_map: dict[str, tuple[str, str]] = {}  # column -> (ref_table, ref_column)
        self._has_more = False
        self._columns: list[str] = []
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.stack = QStackedWidget()

        # Empty state — shown until a table is opened.
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        hint = QLabel(t("data.empty_hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        el.addWidget(hint)
        el.addStretch(1)
        self.stack.addWidget(empty)

        # Data view.
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(16, 10, 16, 0)
        pl.setSpacing(10)

        # Toolbar row: table name · sort caption … page-size · ‹ range › · refresh
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self._title = QLabel("")
        self._title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        bar.addWidget(self._title)
        self._sort_caption = QLabel("")
        self._sort_caption.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        bar.addWidget(self._sort_caption)
        bar.addStretch(1)

        self._page_select = PillSelect("100", max_width=72)
        self._page_select.set_options([(n, n) for n in _PAGE_SIZES])
        self._page_select.set_option_tooltips({n: t("data.page_size") for n in _PAGE_SIZES})
        self._page_select.set_value("100")
        self._page_select.value_changed.connect(self._on_page_size)
        bar.addWidget(self._page_select)

        self._prev = IconToolButton(svg_icon("chevron-left", color=Theme.TEXT_2), t("data.prev"))
        self._prev.clicked.connect(self._on_prev)
        bar.addWidget(self._prev)
        self._range = QLabel("")
        self._range.setStyleSheet(f"color: {Theme.TEXT_2}; font-size: 12px;")
        self._range.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._range.setMinimumWidth(96)
        bar.addWidget(self._range)
        self._next = IconToolButton(svg_icon("chevron-right", color=Theme.TEXT_2), t("data.next"))
        self._next.clicked.connect(self._on_next)
        bar.addWidget(self._next)

        # On-demand exact total (COUNT(*)). Browsing never counts, so this is a
        # quiet text button the user clicks to get the precise row total.
        self._count_btn = QToolButton()
        self._count_btn.setText(t("data.count"))
        self._count_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._count_btn.setMinimumWidth(80)
        self._count_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._count_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; color: {Theme.MUTED};"
            f" font-size: 12px; padding: 0 8px; }}"
            f"QToolButton:hover {{ color: {Theme.TEXT_2}; }}"
        )
        self._count_btn.clicked.connect(self._on_count)
        bar.addWidget(self._count_btn)

        self._refresh = IconToolButton(svg_icon("refresh", color=Theme.TEXT_2), t("data.refresh"))
        self._refresh.clicked.connect(self._reload)
        bar.addWidget(self._refresh)

        self.grid = ResultTableWidget()
        self.grid.meta.setVisible(False)  # the pager's range label is the source of truth here
        # Fold the grid's Export into this single pager row (no separate, sparse
        # toolbar row above the table) — denser layout.
        self.grid.set_toolbar_visible(False)
        bar.addWidget(self.grid.export_menu)
        pl.addLayout(bar)

        # Filter row: a raw WHERE clause (read-only, validated server-side).
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(t("data.filter_placeholder"))
        self._filter.setClearButtonEnabled(True)
        self._filter.returnPressed.connect(self._on_filter)
        # leading magnifier-ish handled by placeholder; keep it simple/compact
        self._filter.setFixedHeight(26)
        # Word-level completion of column names (and a few WHERE keywords) — a plain
        # QLineEdit completer matches the whole line, so we drive the completer on the
        # current word ourselves.
        self._filter_model = QStringListModel(self)
        self._filter_completer = QCompleter(self._filter_model, self)
        self._filter_completer.setWidget(self._filter)
        self._filter_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._filter_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._filter_completer.popup().setStyleSheet(
            f"QListView {{ background: {Theme.SURFACE}; color: {Theme.TEXT};"
            f" border: 1px solid {Theme.BORDER}; border-radius: 8px; padding: 4px; outline: none; }}"
            f"QListView::item {{ padding: 4px 8px; border-radius: 5px; }}"
            f"QListView::item:selected {{ background: {Theme.PANEL_3}; color: {Theme.TEXT}; }}"
        )
        self._filter_completer.activated.connect(self._insert_filter_completion)
        self._filter.textEdited.connect(self._on_filter_text)
        pl.addWidget(self._filter)

        # Sorting is an explicit, deliberate choice via the header right-click menu
        # (Ascending / Descending / Clear), not an accidental click — the active sort
        # shows an up/down arrow on the column.
        self.grid.table.horizontalHeader().setSectionsClickable(False)
        self.grid.set_header_actions_provider(self._sort_actions)
        self.grid.set_cell_actions_provider(self._fk_cell_actions)
        pl.addWidget(self.grid, 1)
        self.stack.addWidget(page)

        outer.addWidget(self.stack)
        # While a page is loading, the Refresh icon spins and the range reads
        # "Loading…" — a quiet, friendly sign that the DB is being queried.
        self._busy = BusyAnimator(
            lambda: self._refresh.setIcon(spinner_icon(self._busy.angle, color=Theme.TEXT_2, size=15))
        )
        self._set_controls_enabled(True)

    # ── public API ────────────────────────────────────────────────────────────

    def open_table(self, connection: str, database: str, table: str) -> None:
        """Start browsing a table from page 1, no sort/filter."""
        self._conn, self._db, self._table = connection, database, table
        self._offset = 0
        self._order_by, self._order_dir = "", "asc"
        self._where = ""
        self._reset_total()
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.blockSignals(False)
        self._title.setText(f"{database + '.' if database else ''}{table}")
        self.grid.set_table_name(table)
        self.stack.setCurrentIndex(1)
        self._reload()

    def show_result(self, result: dict[str, Any]) -> None:
        self._columns = list(result.get("columns") or [])
        self._set_filter_completions()  # WHERE box now completes this table's columns
        rows = result.get("rows") or []
        self.grid.load(
            columns=self._columns,
            rows=rows,
            row_count=result.get("row_count") or len(rows),
            truncated=bool(result.get("truncated")),
            elapsed_ms=float(result.get("elapsed_ms") or 0),
            row_offset=int(result.get("offset") or 0),
        )
        self._has_more = bool(result.get("has_more"))
        self._offset = int(result.get("offset") or 0)
        self._order_by = str(result.get("order_by") or "")
        self._order_dir = str(result.get("order_dir") or "asc")
        self._update_range_label(len(rows))
        self._sort_caption.setText(
            self._t("data.sorted_by", col=self._order_by, dir=self._order_dir.upper())
            if self._order_by else ""
        )
        # Sort indicator: a clear up/down arrow (with a shaft) on the active column's
        # header — not Qt's tiny native triangle.
        header = self.grid.table.horizontalHeader()
        header.setSortIndicatorShown(False)
        from PyQt6.QtGui import QIcon
        table = self.grid.table
        for i in range(len(self._columns)):
            hi = table.horizontalHeaderItem(i)
            if hi is not None:
                hi.setIcon(QIcon())
        if self._order_by and self._order_by in self._columns:
            idx = self._columns.index(self._order_by)
            hi = table.horizontalHeaderItem(idx)
            if hi is not None:
                arrow = "arrow-up" if self._order_dir == "asc" else "arrow-down"
                hi.setIcon(svg_icon(arrow, color=Theme.TEXT_2, size=13))
                # The header icon sits left of the text; widen the column so the name
                # isn't elided to make room for it.
                table.resizeColumnToContents(idx)
        self.set_running(False)
        self._prev.setEnabled(self._offset > 0)
        self._next.setEnabled(self._has_more)

    def set_running(self, running: bool) -> None:
        self._loading = running
        if running:
            self._range.setText(self._t("data.loading"))
            self._busy.start()
        else:
            self._busy.stop()
            self._refresh.setIcon(svg_icon("refresh", color=Theme.TEXT_2, size=15))
        self._set_controls_enabled(not running)

    def browse_filtered(self, connection: str, database: str, table: str, where: str) -> None:
        """Set the table identity and a WHERE filter (e.g. from FK navigation) and
        load from page 1 — used for lazy table docs that haven't browsed yet."""
        self._conn, self._db, self._table = connection, database, table
        self._order_by, self._order_dir = "", "asc"
        self._where = str(where or "")
        self._offset = 0
        self._reset_total()
        self._filter.blockSignals(True)
        self._filter.setText(self._where)
        self._filter.blockSignals(False)
        self._title.setText(f"{database + '.' if database else ''}{table}")
        self.grid.set_table_name(table)
        self.stack.setCurrentIndex(1)
        self._reload()

    def set_foreign_keys(self, fk_map: dict[str, tuple[str, str]]) -> None:
        """column name -> (referenced table, referenced column), for FK navigation."""
        self._fk_map = dict(fk_map or {})

    def _fk_cell_actions(self, row: int, col: int):
        if not (0 <= col < len(self._columns)):
            return []
        column = self._columns[col]
        ref = self._fk_map.get(column)
        if not ref:
            return []
        if not (0 <= row < len(self.grid._rows)):
            return []
        value = self.grid._rows[row].get(column)
        if value is None:
            return []
        ref_table, ref_column = ref
        label = self._t("data.open_referenced", table=ref_table)
        # Capture value by binding default arg — avoids late-binding closure bug
        return [(label, lambda rt=ref_table, rc=ref_column, v=value: self.navigate_fk.emit(rt, rc, v))]

    def show_count(self, total: int) -> None:
        """Display the exact COUNT(*) result (for the current WHERE filter)."""
        self._total = int(total)
        self._total_where = self._where
        self._count_btn.setText(self._t("data.count_total", n=f"{self._total:,}"))
        # Refresh the range caption to fold in the total.
        rows = self.grid.table.rowCount()
        self._update_range_label(rows)

    # ── internals ──────────────────────────────────────────────────────────────

    def _reset_total(self) -> None:
        self._total = None
        self._total_where = None
        self._count_btn.setText(self._t("data.count"))

    def _update_range_label(self, n: int) -> None:
        has_total = self._total is not None and self._total_where == self._where
        if n == 0:
            base = (self._t("data.count_total", n=f"{self._total:,}") if has_total and self._total
                    else self._t("data.no_rows"))
            self._range.setText(base)
            return
        base = self._t("data.rows_range", start=self._offset + 1, end=self._offset + n)
        if has_total:
            base = self._t("data.rows_range_total", start=self._offset + 1,
                           end=self._offset + n, total=f"{self._total:,}")
        self._range.setText(base)

    def _on_count(self) -> None:
        if not self._table or self._loading:
            return
        self.count_requested.emit({
            "connection_name": self._conn,
            "database": self._db,
            "table": self._table,
            "where": self._where,
        })

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self._prev, self._next, self._refresh, self._page_select, self._filter,
                  self._count_btn):
            w.setEnabled(on)

    def _reload(self) -> None:
        if not self._table or self._loading:
            return
        self.set_running(True)
        self.query_requested.emit({
            "connection_name": self._conn,
            "database": self._db,
            "table": self._table,
            "page_size": self._page_size,
            "offset": self._offset,
            "order_by": self._order_by,
            "order_dir": self._order_dir,
            "where": self._where,
        })

    def _on_prev(self) -> None:
        if self._offset > 0:
            self._offset = max(0, self._offset - self._page_size)
            self._reload()

    def _on_next(self) -> None:
        if self._has_more:
            self._offset += self._page_size
            self._reload()

    def _on_page_size(self, value: str) -> None:
        try:
            self._page_size = int(value)
        except (TypeError, ValueError):
            self._page_size = 100
        self._offset = 0
        self._reload()

    def _on_filter(self) -> None:
        self._where = self._filter.text().strip()
        self._offset = 0
        self._reset_total()  # a different filter invalidates the previous total
        self._reload()

    # ── filter (WHERE) completion ────────────────────────────────────────────--

    _WORD = re.compile(r"[A-Za-z_][\w]*$")
    _WHERE_KEYWORDS = ["AND", "OR", "NOT", "LIKE", "IN", "IS", "NULL", "BETWEEN"]

    def _set_filter_completions(self) -> None:
        self._filter_model.setStringList(list(self._columns) + self._WHERE_KEYWORDS)

    def _filter_word(self) -> tuple[str, int]:
        """The identifier word ending at the cursor in the filter box → (word, start)."""
        left = self._filter.text()[: self._filter.cursorPosition()]
        m = self._WORD.search(left)
        return (m.group(0), m.start()) if m else ("", self._filter.cursorPosition())

    def _on_filter_text(self, _text: str) -> None:
        word, _start = self._filter_word()
        popup = self._filter_completer.popup()
        if len(word) < 1 or not self._columns:
            popup.hide()
            return
        self._filter_completer.setCompletionPrefix(word)
        if self._filter_completer.completionCount() == 0:
            popup.hide()
            return
        popup.setCurrentIndex(self._filter_completer.completionModel().index(0, 0))
        self._filter_completer.complete()

    def _insert_filter_completion(self, completion: str) -> None:
        _word, start = self._filter_word()
        text = self._filter.text()
        end = self._filter.cursorPosition()
        self._filter.setText(text[:start] + completion + text[end:])
        self._filter.setCursorPosition(start + len(completion))

    def _sort_actions(self, section: int):
        """Header right-click menu entries for column ``section``: Ascending /
        Descending, plus Clear when that column is the active sort."""
        if not (0 <= section < len(self._columns)):
            return []
        col = self._columns[section]
        actions = [
            (self._t("data.sort_asc"), lambda c=col: self._apply_sort(c, "asc")),
            (self._t("data.sort_desc"), lambda c=col: self._apply_sort(c, "desc")),
        ]
        if self._order_by == col:
            actions.append((self._t("data.sort_clear"), self._clear_sort))
        return actions

    def _apply_sort(self, col: str, direction: str) -> None:
        if self._loading:
            return
        self._order_by, self._order_dir = col, direction
        self._offset = 0
        self._reload()

    def _clear_sort(self) -> None:
        if self._loading:
            return
        self._order_by, self._order_dir = "", "asc"
        self._offset = 0
        self._reload()
