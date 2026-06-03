"""Workbench — the multi-document database-client workspace.

A tabbed document area (DBeaver-style): the user can keep several SQL editors and
several table viewers open at once, plus a pinned History tab. New SQL editors are
opened with the ``+`` button; tables open on double-click in the schema tree. Tabs
are closeable (History excepted) and re-orderable.

The view owns *creation* of documents but stays presentation-only: it re-emits
``run_sql(editor, sql)`` and ``browse_requested(doc, payload)`` up to MainWindow,
which runs them and routes the result back to the originating document.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget

from dbaide.desktop.theme import Theme
from dbaide.desktop.views.query_history import QueryHistoryPanel
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.table_document import TableDocument


class WorkbenchView(QWidget):
    run_sql = pyqtSignal(object, str)        # (SqlTab, sql)
    browse_requested = pyqtSignal(object, dict)  # (TableDocument, payload)
    count_requested = pyqtSignal(object, dict)   # (TableDocument, count payload)
    doc_closed = pyqtSignal(object)          # the closed widget
    navigate_table = pyqtSignal(str)         # FK link → open a related table

    def __init__(self, history_panel: QueryHistoryPanel, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._completions: list[str] = []
        self._query_seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabBar().setProperty("panelTabs", True)
        self.tabs.tabCloseRequested.connect(self._on_close)
        layout.addWidget(self.tabs)

        # History is pinned (always present, not closeable).
        self.history_panel = history_panel
        self._history_index = self.tabs.addTab(history_panel, t("tab.history"))
        self._pin(self._history_index)

        # "+" corner → new SQL editor.
        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setToolTip(t("workbench.new_query"))
        add_btn.setFont(QFont("Inter", 15))
        add_btn.setStyleSheet(
            f"QToolButton {{ border: none; background: transparent; color: {Theme.TEXT_2};"
            f" padding: 0 10px; }} QToolButton:hover {{ color: {Theme.TEXT}; }}"
        )
        add_btn.clicked.connect(lambda: self.new_sql_editor())
        self.tabs.setCornerWidget(add_btn)

        # Start on a single empty SQL editor (DBeaver opens an editor by default).
        self.new_sql_editor()

    # ── pinning / closing ──────────────────────────────────────────────────────

    def _pin(self, index: int) -> None:
        """Remove the close button from a tab so it can't be closed."""
        bar = self.tabs.tabBar()
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            btn = bar.tabButton(index, side)
            if btn is not None:
                btn.deleteLater()
                bar.setTabButton(index, side, None)

    def _on_close(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.history_panel:
            return  # pinned
        self.tabs.removeTab(index)
        self.doc_closed.emit(widget)
        widget.deleteLater()

    # ── SQL editors ─────────────────────────────────────────────────────────────

    def new_sql_editor(self, sql: str = "") -> SqlTab:
        editor = SqlTab()
        editor.set_completions(self._completions)
        if sql:
            editor.set_sql(sql)
        editor.run_requested.connect(
            lambda text, _action, ed=editor: self.run_sql.emit(ed, text)
        )
        self._query_seq += 1
        index = self.tabs.addTab(editor, self._t("workbench.query_n", n=self._query_seq))
        self.tabs.setCurrentIndex(index)
        return editor

    def current_sql_editor(self) -> SqlTab | None:
        w = self.tabs.currentWidget()
        if isinstance(w, SqlTab):
            return w
        # else fall back to the most recent SQL editor, if any
        for i in range(self.tabs.count() - 1, -1, -1):
            if isinstance(self.tabs.widget(i), SqlTab):
                return self.tabs.widget(i)  # type: ignore[return-value]
        return None

    def ensure_sql_editor(self) -> SqlTab:
        return self.current_sql_editor() or self.new_sql_editor()

    def open_sql(self, sql: str) -> SqlTab:
        """Load SQL into the current editor if it's empty, else open a new one."""
        cur = self.current_sql_editor()
        if cur is not None and not cur.editor.toPlainText().strip():
            cur.set_sql(sql)
            self.tabs.setCurrentWidget(cur)
            return cur
        return self.new_sql_editor(sql)

    def set_sql_completions(self, names: list[str]) -> None:
        self._completions = list(names)
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SqlTab):
                w.set_completions(self._completions)

    # ── table documents ─────────────────────────────────────────────────────────

    def open_table(self, connection: str, database: str, table: str,
                   columns: list[dict[str, Any]],
                   relations: dict[str, list[dict[str, Any]]] | None = None,
                   indexes: list[dict[str, Any]] | None = None) -> TableDocument:
        target_key = TableDocument.key(connection, database, table)
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableDocument) and w.doc_key == target_key:
                # Already open — just bring it forward, keeping the user's sub-tab.
                self.tabs.setCurrentIndex(i)
                return w
        doc = TableDocument(connection, database, table)
        doc.query_requested.connect(lambda payload, d=doc: self.browse_requested.emit(d, payload))
        doc.count_requested.connect(lambda payload, d=doc: self.count_requested.emit(d, payload))
        doc.navigate_table.connect(self.navigate_table.emit)
        index = self.tabs.addTab(doc, table)
        self.tabs.setCurrentIndex(index)
        doc.open(columns, relations, indexes)
        return doc

    # ── focus helpers (used by MainWindow.switch_tab) ────────────────────────────

    def focus_sql(self) -> None:
        self.tabs.setCurrentWidget(self.ensure_sql_editor())

    def focus_data(self) -> None:
        w = self.tabs.currentWidget()
        if isinstance(w, TableDocument):
            w.focus_data()
            return
        for i in range(self.tabs.count() - 1, -1, -1):
            if isinstance(self.tabs.widget(i), TableDocument):
                self.tabs.setCurrentIndex(i)
                return

    def focus_history(self) -> None:
        self.tabs.setCurrentWidget(self.history_panel)
