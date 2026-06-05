"""A table viewer document — one open table, with Data and Structure sub-tabs.

Mirrors DBeaver's per-table editor (Data / Properties / DDL). Each opened table
gets its own ``TableDocument`` in the Workbench, so several tables can stay open
at once. The Data grid drives ``query_requested`` (re-emitted up to MainWindow);
Structure is rendered instantly from the schema columns already in memory.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from dbaide.desktop.theme import Theme
from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.structure_panel import StructurePanel


class TableDocument(QWidget):
    query_requested = pyqtSignal(dict)
    count_requested = pyqtSignal(dict)
    ddl_requested = pyqtSignal(dict)   # fetch the real CREATE TABLE DDL from the DB
    navigate_table = pyqtSignal(str)  # bubbled from the Structure panel's FK links
    navigate_fk = pyqtSignal(str, str, object)  # (ref_table, ref_column, value)
    note_edited = pyqtSignal(dict)  # inline note edit {database, table, column, note}

    def __init__(self, connection: str, database: str, table: str, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self.connection = connection
        self.database = database
        self.table = table

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("panelTabs", True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tabs.setStyleSheet(
            f"QTabWidget {{ background: {Theme.SURFACE}; }}"
            f"QTabWidget::tab-bar {{ background: {Theme.SURFACE}; }}"
            f"QTabWidget::pane {{ border: none; background: {Theme.SURFACE}; }}"
        )
        self.data = DataBrowser()
        self.data.query_requested.connect(self.query_requested.emit)
        self.data.count_requested.connect(self.count_requested.emit)
        self.data.navigate_fk.connect(self.navigate_fk.emit)
        self.structure = StructurePanel()
        self.structure.navigate_table.connect(self.navigate_table.emit)
        self.structure.note_edited.connect(self._on_note_edited)
        # Structure first — opening a table shows its (offline, instant) structure;
        # the Data tab issues its query lazily, only when the user actually opens it.
        self._structure_index = self.tabs.addTab(self.structure, t("tab.structure"))
        self._data_index = self.tabs.addTab(self.data, t("tab.data"))
        self._data_loaded = False
        self._ddl_loaded = False
        self.tabs.currentChanged.connect(self._on_subtab)
        layout.addWidget(self.tabs)

    @staticmethod
    def key(connection: str, database: str, table: str) -> str:
        return f"{connection}\x1f{database}\x1f{table}"

    @property
    def doc_key(self) -> str:
        return self.key(self.connection, self.database, self.table)

    def open(self, columns: list[dict[str, Any]],
             relations: dict[str, list[dict[str, Any]]] | None = None,
             indexes: list[dict[str, Any]] | None = None,
             table_note: str = "") -> None:
        """Render the offline structure and show it. No query runs until the user
        opens the Data tab (see ``_ensure_data``)."""
        self.structure.show_table(self.table, columns or [], relations or {}, indexes or [],
                                  table_note=table_note)
        # Feed the data grid the outgoing FK map so cells can navigate to refs.
        fk_map = {
            fk.get("column"): (fk.get("ref_table"), fk.get("ref_column"))
            for fk in ((relations or {}).get("foreign_keys") or [])
            if fk.get("column") and fk.get("ref_table")
        }
        self.data.set_foreign_keys(fk_map)
        self.tabs.setCurrentIndex(self._structure_index)
        # Structure is the default tab → fetch the real CREATE TABLE DDL once (the
        # generated skeleton is shown meanwhile). MainWindow runs it and calls show_ddl.
        if not self._ddl_loaded:
            self._ddl_loaded = True
            self.ddl_requested.emit({
                "connection_name": self.connection,
                "database": self.database,
                "table": self.table,
            })

    def show_ddl(self, ddl: str) -> None:
        self.structure.set_ddl(ddl)

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

    def _on_note_edited(self, column: str, text: str) -> None:
        # The Structure panel knows only table/column; add this document's database
        # before bubbling the inline edit up to the window for persistence.
        self.note_edited.emit({
            "database": self.database,
            "table": self.table,
            "column": column,
            "note": text,
        })
