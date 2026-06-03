"""Workbench — the database-client workspace (separate from the AI Assistant mode).

Hosts the SQL editor and the table data browser as document tabs. Kept as its own
view so it can grow into a multi-document area (several open queries / table tabs)
without entangling the conversation UI.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from dbaide.desktop.views.data_browser import DataBrowser
from dbaide.desktop.views.sql_tab import SqlTab


class WorkbenchView(QWidget):
    def __init__(self, sql_tab: SqlTab, data_tab: DataBrowser, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("panelTabs", True)  # quiet, rounded document tabs
        self.sql_tab = sql_tab
        self.data_tab = data_tab
        self._sql_index = self.tabs.addTab(sql_tab, t("tab.sql"))
        self._data_index = self.tabs.addTab(data_tab, t("tab.data"))
        layout.addWidget(self.tabs)

    def focus_sql(self) -> None:
        self.tabs.setCurrentIndex(self._sql_index)

    def focus_data(self) -> None:
        self.tabs.setCurrentIndex(self._data_index)
