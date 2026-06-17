"""A table viewer document — one open table, with Data and Structure sub-tabs.

Mirrors DBeaver's per-table editor (Data / Properties / DDL). Each opened table
gets its own ``TableDocument`` in the Workbench, so several tables can stay open
at once. The Data grid drives ``query_requested`` (re-emitted up to MainWindow);
Structure is rendered instantly from the schema columns already in memory.

The sub-tab selector is a compact icon-only segment bar — visually distinct from
the outer Workbench tab row so the two levels don't look like stacked tabs.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.doc_tab import DocTab
from dbaide.desktop.views.structure_panel import StructurePanel


class TableDocument(QWidget):
    query_requested = pyqtSignal(dict)
    count_requested = pyqtSignal(dict)
    ddl_requested = pyqtSignal(dict)   # fetch the real CREATE TABLE DDL from the DB
    export_all_requested = pyqtSignal(dict)  # export full result (no LIMIT)
    navigate_table = pyqtSignal(str)  # bubbled from the Structure panel's FK links
    navigate_fk = pyqtSignal(str, str, object)  # (ref_table, ref_column, value)

    doc_requested = pyqtSignal(str)  # asset path — request markdown load

    def __init__(self, connection: str, database: str, table: str, *, dialect: str = "generic", parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        from dbaide.desktop.components.icons import svg_icon
        from dbaide.desktop.theme import Theme
        self.connection = connection
        self.database = database
        self.table = table
        self._dialect = dialect

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Compact icon-only segment bar (not a QTabWidget — avoids stacked-tabs look) --
        self.bar = QTabBar()
        self.bar.setProperty("subtabBar", True)
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setIconSize(QSize(14, 14))

        self.doc_tab = DocTab(table)
        self._doc_loaded = False
        self._doc_index = self.bar.addTab(svg_icon("file-text", color=Theme.TEXT_2, size=14), "")
        self.bar.setTabToolTip(self._doc_index, t("tab.doc"))

        self.structure = StructurePanel()
        self.structure.navigate_table.connect(self.navigate_table.emit)
        self._structure_index = self.bar.addTab(svg_icon("columns", color=Theme.TEXT_2, size=14), "")
        self.bar.setTabToolTip(self._structure_index, t("tab.structure"))

        self.data = DataBrowser()
        self.data.query_requested.connect(self.query_requested.emit)
        self.data.count_requested.connect(self.count_requested.emit)
        self.data.export_all_requested.connect(self.export_all_requested.emit)
        self.data.navigate_fk.connect(self.navigate_fk.emit)
        self._data_index = self.bar.addTab(svg_icon("table", color=Theme.TEXT_2, size=14), "")
        self.bar.setTabToolTip(self._data_index, t("tab.data"))

        self._data_loaded = False
        self._ddl_loaded = False

        # -- Stacked content pages --
        self.stack = QStackedWidget()
        self.stack.addWidget(self.doc_tab)    # 0 = doc
        self.stack.addWidget(self.structure)   # 1 = structure
        self.stack.addWidget(self.data)        # 2 = data

        self.bar.currentChanged.connect(self._on_bar_changed)

        # Layout: segment bar flush-left in a thin toolbar row, content below.
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(6, 4, 0, 2)
        bar_row.addWidget(self.bar)
        bar_row.addStretch()
        layout.addLayout(bar_row)
        layout.addWidget(self.stack)

    @staticmethod
    def key(connection: str, database: str, table: str) -> str:
        return f"{connection}\x1f{database}\x1f{table}"

    @property
    def doc_key(self) -> str:
        return self.key(self.connection, self.database, self.table)

    def open(self, columns: list[dict[str, Any]],
             relations: dict[str, list[dict[str, Any]]] | None = None,
             indexes: list[dict[str, Any]] | None = None) -> None:
        """Render the offline structure and show it. No query runs until the user
        opens the Data tab (see ``_ensure_data``)."""
        self.structure.show_table(self.table, columns or [], relations or {}, indexes or [])
        fk_map = {
            fk.get("column"): (fk.get("ref_table"), fk.get("ref_column"))
            for fk in ((relations or {}).get("foreign_keys") or [])
            if fk.get("column") and fk.get("ref_table")
        }
        self.data.set_foreign_keys(fk_map)
        self.bar.setCurrentIndex(self._structure_index)
        if not self._ddl_loaded:
            self._ddl_loaded = True
            self.ddl_requested.emit({
                "connection_name": self.connection,
                "database": self.database,
                "table": self.table,
            })

    def show_ddl(self, ddl: str) -> None:
        self.structure.set_ddl(ddl)

    def _on_bar_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == self._data_index:
            self._ensure_data()
        elif index == self._doc_index:
            self._ensure_doc()

    def _ensure_data(self) -> None:
        """Issue the first data query the first time the Data tab is opened."""
        if not self._data_loaded:
            self._data_loaded = True
            self.data.open_table(self.connection, self.database, self.table, dialect=self._dialect)

    def _ensure_doc(self) -> None:
        """Request the asset markdown the first time the Doc tab is opened."""
        if not self._doc_loaded:
            self._doc_loaded = True
            path = self._asset_path()
            if path:
                self.doc_requested.emit(path)

    def _asset_path(self) -> str:
        parts = [p for p in (self.connection, self.database, self.table) if p]
        return ".".join(parts) if len(parts) >= 2 else ""

    def show_doc(self, markdown: str) -> None:
        self.doc_tab.set_content(self.table, markdown)

    def focus_data(self) -> None:
        self.bar.setCurrentIndex(self._data_index)

    def focus_doc(self) -> None:
        self.bar.setCurrentIndex(self._doc_index)

    def browse_with_filter(self, where: str) -> None:
        """Open the Data tab and load it filtered (used by FK navigation)."""
        self._data_loaded = True
        self.bar.setCurrentIndex(self._data_index)
        self.data.browse_filtered(self.connection, self.database, self.table, where, dialect=self._dialect)

    def focus_structure(self) -> None:
        self.bar.setCurrentIndex(self._structure_index)

    def set_running(self, running: bool) -> None:
        self.data.set_running(running)

    def show_result(self, result: dict[str, Any]) -> None:
        self.data.show_result(result)

    def show_count(self, total: int) -> None:
        self.data.show_count(total)
