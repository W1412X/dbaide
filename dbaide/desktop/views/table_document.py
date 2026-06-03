"""A table viewer document — one open table, with Data and Structure sub-tabs.

Mirrors DBeaver's per-table editor (Data / Properties / DDL). Each opened table
gets its own ``TableDocument`` in the Workbench, so several tables can stay open
at once. The Data grid drives ``query_requested`` (re-emitted up to MainWindow);
Structure is rendered instantly from the schema columns already in memory.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.structure_panel import StructurePanel


class TableDocument(QWidget):
    query_requested = pyqtSignal(dict)
    navigate_table = pyqtSignal(str)  # bubbled from the Structure panel's FK links

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
        self.data = DataBrowser()
        self.data.query_requested.connect(self.query_requested.emit)
        self.structure = StructurePanel()
        self.structure.navigate_table.connect(self.navigate_table.emit)
        self._data_index = self.tabs.addTab(self.data, t("tab.data"))
        self._structure_index = self.tabs.addTab(self.structure, t("tab.structure"))
        layout.addWidget(self.tabs)

    @staticmethod
    def key(connection: str, database: str, table: str) -> str:
        return f"{connection}\x1f{database}\x1f{table}"

    @property
    def doc_key(self) -> str:
        return self.key(self.connection, self.database, self.table)

    def open(self, columns: list[dict[str, Any]],
             relations: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """Render structure from columns and kick off the first data page."""
        self.structure.show_table(self.table, columns or [], relations or {})
        self.data.open_table(self.connection, self.database, self.table)

    def focus_data(self) -> None:
        self.tabs.setCurrentIndex(self._data_index)

    def focus_structure(self) -> None:
        self.tabs.setCurrentIndex(self._structure_index)

    def set_running(self, running: bool) -> None:
        self.data.set_running(running)

    def show_result(self, result: dict[str, Any]) -> None:
        self.data.show_result(result)
