"""Table data browser — a read-only, paginated, sortable grid for one table.

Double-clicking a table in the schema tree opens it here. The widget owns the
browse *state* (table, page, sort, filter) and emits ``query_requested`` whenever
that state changes; MainWindow runs it as a ``browse_table`` one-off and feeds the
result back via ``show_result``. Pagination is cursor-style (LIMIT/OFFSET, "more =
a full page returned") so it stays cheap on large tables — no COUNT(*).
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.menu import PillSelect
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme

_PAGE_SIZES = ("50", "100", "200", "500")


class DataBrowser(QWidget):
    query_requested = pyqtSignal(dict)  # browse_table payload

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
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)

        # Toolbar row: table name · sort caption … page-size · ‹ range › · refresh
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
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

        self._refresh = IconToolButton(svg_icon("refresh", color=Theme.TEXT_2), t("data.refresh"))
        self._refresh.clicked.connect(self._reload)
        bar.addWidget(self._refresh)
        pl.addLayout(bar)

        # Filter row: a raw WHERE clause (read-only, validated server-side).
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(t("data.filter_placeholder"))
        self._filter.setClearButtonEnabled(True)
        self._filter.returnPressed.connect(self._on_filter)
        # leading magnifier-ish handled by placeholder; keep it simple/compact
        self._filter.setFixedHeight(30)
        pl.addWidget(self._filter)

        self.grid = ResultTableWidget()
        self.grid.meta.setVisible(False)  # the pager's range label is the source of truth here
        # Sorting: clicking a column header re-queries ORDER BY that column.
        self.grid.table.horizontalHeader().setSectionsClickable(True)
        self.grid.table.horizontalHeader().sectionClicked.connect(self._on_sort)
        pl.addWidget(self.grid, 1)
        self.stack.addWidget(page)

        outer.addWidget(self.stack)
        self._set_controls_enabled(True)

    # ── public API ────────────────────────────────────────────────────────────

    def open_table(self, connection: str, database: str, table: str) -> None:
        """Start browsing a table from page 1, no sort/filter."""
        self._conn, self._db, self._table = connection, database, table
        self._offset = 0
        self._order_by, self._order_dir = "", "asc"
        self._where = ""
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.blockSignals(False)
        self._title.setText(f"{database + '.' if database else ''}{table}")
        self.grid.set_table_name(table)
        self.stack.setCurrentIndex(1)
        self._reload()

    def show_result(self, result: dict[str, Any]) -> None:
        self._columns = list(result.get("columns") or [])
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
        n = len(rows)
        if n == 0:
            self._range.setText(self._t("data.no_rows"))
        else:
            self._range.setText(self._t("data.rows_range", start=self._offset + 1, end=self._offset + n))
        self._sort_caption.setText(
            self._t("data.sorted_by", col=self._order_by, dir=self._order_dir.upper())
            if self._order_by else ""
        )
        self.set_running(False)
        self._prev.setEnabled(self._offset > 0)
        self._next.setEnabled(self._has_more)

    def set_running(self, running: bool) -> None:
        self._loading = running
        self._set_controls_enabled(not running)

    # ── internals ──────────────────────────────────────────────────────────────

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self._prev, self._next, self._refresh, self._page_select, self._filter):
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
        self._reload()

    def _on_sort(self, index: int) -> None:
        if not (0 <= index < len(self._columns)) or self._loading:
            return
        col = self._columns[index]
        if col == self._order_by:
            self._order_dir = "desc" if self._order_dir == "asc" else "asc"
        else:
            self._order_by, self._order_dir = col, "asc"
        self._offset = 0
        self._reload()
