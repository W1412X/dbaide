"""A table viewer document — one open table, with Data and Structure sub-tabs.

Mirrors DBeaver's per-table editor (Data / Properties / DDL). Each opened table
gets its own ``TableDocument`` in the Workbench, so several tables can stay open
at once. The Data grid drives ``query_requested`` (re-emitted up to MainWindow);
Structure is rendered instantly from the schema columns already in memory.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.structure_panel import StructurePanel


class TableDocument(QWidget):
    query_requested = pyqtSignal(dict)
    count_requested = pyqtSignal(dict)
    navigate_table = pyqtSignal(str)  # bubbled from the Structure panel's FK links
    navigate_fk = pyqtSignal(str, str, object)  # (ref_table, ref_column, value)
    ask_ai_requested = pyqtSignal(str, str)  # (table_name, schema_summary)

    def __init__(self, connection: str, database: str, table: str, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self.connection = connection
        self.database = database
        self.table = table

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Ask AI toolbar row (at the very top)
        ask_row = QHBoxLayout()
        ask_row.setContentsMargins(2, 0, 2, 6)
        ask_row.addStretch(1)
        ask_btn = compact_button(t("doc.ask_ai"), width=110)
        ask_btn.setToolTip(t("doc.ask_ai_tooltip"))
        ask_btn.setIcon(svg_icon("terminal", color=Theme.TEXT_2, size=13))
        ask_btn.setIconSize(QSize(13, 13))
        ask_btn.clicked.connect(self._on_ask_ai)
        ask_row.addWidget(ask_btn)
        layout.addLayout(ask_row)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("panelTabs", True)
        self.data = DataBrowser()
        self.data.query_requested.connect(self.query_requested.emit)
        self.data.count_requested.connect(self.count_requested.emit)
        self.data.navigate_fk.connect(self.navigate_fk.emit)
        self.structure = StructurePanel()
        self.structure.navigate_table.connect(self.navigate_table.emit)
        # Structure first — opening a table shows its (offline, instant) structure;
        # the Data tab issues its query lazily, only when the user actually opens it.
        self._structure_index = self.tabs.addTab(self.structure, t("tab.structure"))
        self._data_index = self.tabs.addTab(self.data, t("tab.data"))
        self._data_loaded = False
        self.tabs.currentChanged.connect(self._on_subtab)
        layout.addWidget(self.tabs)

    def _on_ask_ai(self) -> None:
        """Emit ask_ai_requested with the table name and a brief schema summary."""
        # structure._cols._rows uses "Column"/"Type"/"Key" keys (as set by show_table)
        cols = ", ".join(c.get("Column", "") for c in self.structure._cols._rows if c.get("Column"))
        schema_summary = f"Table: {self.table}\nColumns: {cols}"
        self.ask_ai_requested.emit(self.table, schema_summary)

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
        # Feed the data grid the outgoing FK map so cells can navigate to refs.
        fk_map = {
            fk.get("column"): (fk.get("ref_table"), fk.get("ref_column"))
            for fk in ((relations or {}).get("foreign_keys") or [])
            if fk.get("column") and fk.get("ref_table")
        }
        self.data.set_foreign_keys(fk_map)
        self.tabs.setCurrentIndex(self._structure_index)

    def _on_subtab(self, index: int) -> None:
        if index == self._data_index:
            self._ensure_data()

    def _ensure_data(self) -> None:
        """Issue the first data query the first time the Data tab is opened."""
        if not self._data_loaded:
            self._data_loaded = True
            self.data.open_table(self.connection, self.database, self.table)

    def focus_data(self) -> None:
        self.tabs.setCurrentIndex(self._data_index)
        self._ensure_data()

    def browse_with_filter(self, where: str) -> None:
        """Open the Data tab and load it filtered (used by FK navigation)."""
        self._data_loaded = True  # we load explicitly below; skip the lazy reload
        self.tabs.setCurrentIndex(self._data_index)
        self.data.browse_filtered(self.connection, self.database, self.table, where)

    def focus_structure(self) -> None:
        self.tabs.setCurrentIndex(self._structure_index)

    def set_running(self, running: bool) -> None:
        self.data.set_running(running)

    def show_result(self, result: dict[str, Any]) -> None:
        self.data.show_result(result)

    def show_count(self, total: int) -> None:
        self.data.show_count(total)
