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

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QProxyStyle,
    QStyle,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.desktop.views.doc_tab import DocTab
from dbaide.desktop.views.query_history import QueryHistoryPanel


class _RightCloseTabStyle(QProxyStyle):
    """Forces tab close buttons onto the right side. The native macOS style puts
    them on the LEFT (Safari convention), which looks wrong for document tabs and
    breaks our right-side pinning logic."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_TabBar_CloseButtonPosition:
            return QTabBar.ButtonPosition.RightSide.value
        return super().styleHint(hint, option, widget, returnData)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802
        # Pin the close-button indicator to one size — the native (macOS) metric is
        # inconsistent, so close buttons rendered at slightly different sizes per tab.
        if metric in (QStyle.PixelMetric.PM_TabCloseIndicatorWidth,
                      QStyle.PixelMetric.PM_TabCloseIndicatorHeight):
            return 16
        return super().pixelMetric(metric, option, widget)
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.table_document import TableDocument


class WorkbenchView(QWidget):
    run_sql = pyqtSignal(object, str)        # (SqlTab, sql)
    explain_sql = pyqtSignal(object, str)    # (SqlTab, sql) — show query plan
    browse_requested = pyqtSignal(object, dict)  # (TableDocument, payload)
    count_requested = pyqtSignal(object, dict)   # (TableDocument, count payload)
    ddl_requested = pyqtSignal(object, dict)     # (TableDocument, ddl payload)
    doc_closed = pyqtSignal(object)          # the closed widget
    navigate_table = pyqtSignal(str)         # FK link → open a related table
    navigate_fk = pyqtSignal(str, str, object)  # data-cell FK → open referenced row
    doc_requested = pyqtSignal(str)          # path — emitted when a DocTab is activated

    def __init__(self, history_panel: QueryHistoryPanel, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._completions: list[str] = []
        self._schema: dict = {}
        self._query_seq = 0
        self._doc_tabs: dict[str, DocTab] = {}  # path → DocTab

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabBar().setProperty("panelTabs", True)
        # Force close buttons to the right (native macOS puts them left). No-arg
        # QProxyStyle wraps the app's default style WITHOUT owning it (passing an
        # existing style double-frees it on teardown → crash). Keep a Python ref so
        # the style outlives the tab bar's use of it.
        self._tab_close_style = _RightCloseTabStyle()
        self.tabs.tabBar().setStyle(self._tab_close_style)
        self.tabs.tabCloseRequested.connect(self._on_close)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The tab bar itself must stay on the global panelTabs stylesheet. Setting
        # a widget-local QTabBar stylesheet here makes Qt drop the app-level tab
        # subcontrol rules, so document tabs fall back to native gray colors.
        self.tabs.setStyleSheet(
            f"QTabWidget {{ background: {Theme.SURFACE}; }}"
            f"QTabWidget::tab-bar {{ background: {Theme.SURFACE}; }}"
            f"QTabWidget::pane {{ border: none; background: {Theme.SURFACE}; }}"
        )
        layout.addWidget(self.tabs)

        # History opens on demand from the corner icon (it no longer occupies a
        # permanent tab — that just cluttered the bar). The panel widget is owned by
        # MainWindow and reused.
        self.history_panel = history_panel

        # Top-right corner: a "+" (new SQL editor) icon and, to its right, a clock
        # (query history) icon — both compact, sized to sit in the slim tab row.
        new_btn = self._corner_icon("plus", t("workbench.new_query"), lambda: self.new_sql_editor())
        hist_btn = self._corner_icon("clock", t("tab.history"), self.focus_history)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(4, 0, 2, 0)   # right≈flush so the icons line up with the editor strip
        hl.setSpacing(2)
        hl.addWidget(new_btn)
        hl.addWidget(hist_btn)
        self.tabs.setCornerWidget(holder, Qt.Corner.TopRightCorner)
        self.tabs.currentChanged.connect(self._on_workbench_tab_changed)

        # Start on a single empty SQL editor (DBeaver opens an editor by default).
        self.new_sql_editor()

    def _corner_icon(self, icon_name: str, tooltip: str, on_click) -> QToolButton:
        """A compact icon button for the tab-bar corner. Overrides the global
        QToolButton box (padding/min-max-height) so the icon isn't squeezed and the
        button fits the slim tab row."""
        btn = QToolButton()
        btn.setIcon(svg_icon(icon_name, color=Theme.TEXT_2, size=14))
        btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 26px wide to line up with the editor's right-edge action strip (Run/Explain/
        # Format are 26px), so the corner icons and that strip read as one column.
        btn.setFixedSize(26, 20)
        btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 5px;"
            f" padding: 0; margin: 0; min-width: 26px; max-width: 26px;"
            f" min-height: 20px; max-height: 20px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        )
        btn.clicked.connect(lambda _checked=False: on_click())
        return btn

    # ── pinning / closing ──────────────────────────────────────────────────────

    def _pin(self, index: int) -> None:
        """Remove the close button from a tab so it can't be closed."""
        bar = self.tabs.tabBar()
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            btn = bar.tabButton(index, side)
            if btn is not None:
                btn.deleteLater()
                bar.setTabButton(index, side, None)

    def close_current(self) -> None:
        """Close the current document (no-op on the pinned History tab)."""
        self._on_close(self.tabs.currentIndex())

    def close_table_docs(self) -> None:
        """Close every open table viewer AND DocTab — used when the connection
        changes, since their data/structure/docs belong to the old connection.
        SQL editors (portable text) and the pinned History are kept."""
        for i in range(self.tabs.count() - 1, -1, -1):
            w = self.tabs.widget(i)
            if isinstance(w, (TableDocument, DocTab)):
                # Clean up DocTab registry
                if isinstance(w, DocTab):
                    for path, tab in list(self._doc_tabs.items()):
                        if tab is w:
                            del self._doc_tabs[path]
                            break
                self.tabs.removeTab(i)
                self.doc_closed.emit(w)
                w.deleteLater()

    # ── SQL editors ─────────────────────────────────────────────────────────────

    def new_sql_editor(self, sql: str = "") -> SqlTab:
        editor = SqlTab()
        if self._schema:
            editor.set_schema(self._schema)
        elif self._completions:
            editor.set_completions(self._completions)
        if sql:
            editor.set_sql(sql)
        editor.run_requested.connect(
            lambda text, action, ed=editor: (
                self.explain_sql.emit(ed, text) if action == "explain"
                else self.run_sql.emit(ed, text)
            )
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

    def set_sql_schema(self, schema: dict) -> None:
        """Structured schema for context-aware (db/table/column) completion."""
        self._schema = dict(schema or {})
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SqlTab):
                w.set_schema(self._schema)

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
        doc.ddl_requested.connect(lambda payload, d=doc: self.ddl_requested.emit(d, payload))
        doc.navigate_table.connect(self.navigate_table.emit)
        doc.navigate_fk.connect(self.navigate_fk.emit)
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
        """Open the query-history tab (adding it on demand) and bring it forward.
        Once open it's pinned (un-closeable) via the _on_close guard; the corner
        clock icon re-focuses it."""
        idx = self.tabs.indexOf(self.history_panel)
        if idx < 0:
            from dbaide.i18n import t
            idx = self.tabs.addTab(self.history_panel, t("tab.history"))
            self._pin(idx)
        self.tabs.setCurrentIndex(idx)

    # ── Doc tabs (asset markdown viewer) ────────────────────────────────────────

    def open_doc(self, path: str, title: str, markdown: str = "") -> DocTab:
        """Open (or focus) a DocTab for the given asset path.

        At most one DocTab per path. If already open, just bring it forward.
        The tab title is the last segment of the dot-separated path (table name).
        """
        if path in self._doc_tabs:
            w = self._doc_tabs[path]
            idx = self.tabs.indexOf(w)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
                return w
            # Tab was closed externally — clean up registry
            del self._doc_tabs[path]

        tab_title = path.split(".")[-1] if path else title
        doc = DocTab(title, markdown)
        self._doc_tabs[path] = doc
        index = self.tabs.addTab(doc, tab_title)
        self.tabs.setCurrentIndex(index)
        return doc

    def update_doc(self, path: str, markdown: str) -> None:
        """Update the content of an open DocTab for *path* (no-op if not open)."""
        doc = self._doc_tabs.get(path)
        if doc is None:
            return
        title = path.split(".")[-1] if path else path
        doc.set_content(title, markdown)

    def _on_workbench_tab_changed(self, index: int) -> None:
        """When a DocTab becomes active, emit doc_requested so MainWindow can
        lazily load the markdown if it hasn't been loaded yet."""
        w = self.tabs.widget(index)
        if isinstance(w, DocTab):
            # Find its registered path
            for path, tab in list(self._doc_tabs.items()):
                if tab is w:
                    self.doc_requested.emit(path)
                    break

    # ── close helpers (override to also clean up _doc_tabs) ─────────────────────

    def _on_close(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.history_panel:
            return  # pinned
        # Remove from doc_tabs registry if it's a DocTab
        if isinstance(widget, DocTab):
            for path, tab in list(self._doc_tabs.items()):
                if tab is widget:
                    del self._doc_tabs[path]
                    break
        self.tabs.removeTab(index)
        self.doc_closed.emit(widget)
        widget.deleteLater()
