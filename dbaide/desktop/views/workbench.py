"""Workbench — the database-client workspace (separate from the AI Assistant mode).

Hosts the SQL editor and the table data browser as document tabs. Kept as its own
view so it can grow into a multi-document area (several open queries / table tabs)
without entangling the conversation UI.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.query_history import QueryHistoryPanel
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.structure_panel import StructurePanel


class WorkbenchView(QWidget):
    def __init__(self, sql_tab: SqlTab, data_tab: DataBrowser,
                 structure_panel: StructurePanel, history_panel: QueryHistoryPanel,
                 parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("panelTabs", True)  # quiet, rounded document tabs
        self.sql_tab = sql_tab
        self.data_tab = data_tab
        self.structure_panel = structure_panel
        self.history_panel = history_panel
        self._sql_index = self.tabs.addTab(sql_tab, t("tab.sql"))
        self._data_index = self.tabs.addTab(data_tab, t("tab.data"))
        self._structure_index = self.tabs.addTab(structure_panel, t("tab.structure"))
        self._history_index = self.tabs.addTab(history_panel, t("tab.history"))
        layout.addWidget(self.tabs)

    def focus_sql(self) -> None:
        self.tabs.setCurrentIndex(self._sql_index)

    def focus_data(self) -> None:
        self.tabs.setCurrentIndex(self._data_index)

    def focus_structure(self) -> None:
        self.tabs.setCurrentIndex(self._structure_index)

    def focus_history(self) -> None:
        self.tabs.setCurrentIndex(self._history_index)
