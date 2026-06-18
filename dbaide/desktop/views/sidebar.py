from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import SectionLabel, compact_button
from dbaide.desktop.components.icons import more_icon, svg_icon, svg_pixmap
from dbaide.desktop.components.session_list import SessionList
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon, spinner_pixmap
from dbaide.desktop.theme import Theme


class Sidebar(QWidget):
    schema_preview = pyqtSignal(dict)
    schema_selected = pyqtSignal(dict)
    semantic_search_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    generate_sql = pyqtSignal(dict, str)  # (table node, template kind)
    edit_note = pyqtSignal(dict)  # edit the user note for a db/table/column node
    refresh_requested = pyqtSignal(dict)  # refresh a db/table from the live catalog
    enrich_requested = pyqtSignal(dict)  # build the optional enrichment for a db/table node
    backup_requested = pyqtSignal(dict)  # trigger backup for a table/database node

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        from dbaide.i18n import t

        self.context_tabs = QTabBar()
        self.context_tabs.setObjectName("sidebarContextTabs")
        self.context_tabs.setProperty("sidebarSwitch", True)
        self.context_tabs.setDrawBase(False)
        self.context_tabs.setUsesScrollButtons(False)
        self.context_tabs.setExpanding(True)
        self.context_tabs.setIconSize(QSize(14, 14))
        self.context_tabs.setFixedHeight(34)
        self.context_tabs.addTab(svg_icon("message-circle", color=Theme.TEXT_2, size=14), t("sidebar.chats"))
        self.context_tabs.addTab(svg_icon("database", color=Theme.TEXT_2, size=14), t("sidebar.schema"))
        self.context_tabs.currentChanged.connect(self._on_context_tab_changed)
        layout.addWidget(self.context_tabs)

        # Chats and Schema share the same sidebar shell. In Chat mode the user can
        # switch between the two; in Workbench mode Schema is forced.
        split = QSplitter(Qt.Orientation.Vertical)
        self._split = split
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)

        self.chats = SessionList()
        split.addWidget(self.chats)

        schema_panel = QWidget()
        self._schema_panel = schema_panel
        schema_layout = QVBoxLayout(schema_panel)
        schema_layout.setContentsMargins(0, 0, 0, 0)
        schema_layout.setSpacing(8)
        schema_layout.addWidget(SectionLabel(t("sidebar.schema_heading")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("sidebar.filter"))
        self.search.setToolTip(t("sidebar.filter.hint"))
        self.search.setFixedHeight(32)
        # A precisely-placed magnifier overlaid at the left (Qt's addAction leaves an
        # awkward double gap between icon and text); the text is inset to clear it.
        self.search.setStyleSheet(
            f"QLineEdit {{ background:{Theme.PANEL}; border:1px solid {Theme.BORDER};"
            f" border-radius:8px; padding:0 10px 0 31px; }}"
            f"QLineEdit:focus {{ border:1px solid {Theme.FOCUS}; }}"
        )
        search_icon = QLabel(self.search)
        search_icon.setPixmap(svg_pixmap("search", color=Theme.MUTED, size=15))
        search_icon.setFixedSize(15, 15)
        search_icon.move(10, (32 - 15) // 2)
        search_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        search_icon.setStyleSheet("background: transparent; border: none;")
        self.search.textChanged.connect(self._filter_tree)
        self.search.returnPressed.connect(self._semantic_search)
        schema_layout.addWidget(self.search)

        self._build_progress_token = 0
        self._build_progress_active = False
        self._build_db_totals: dict[str, int] = {}
        self._build_db_completed: dict[str, int] = {}
        self._build_progress_pending: object | None = None
        self._build_progress_flush = QTimer(self)
        self._build_progress_flush.setSingleShot(True)
        self._build_progress_flush.setInterval(120)
        self._build_progress_flush.timeout.connect(self._flush_build_progress)
        self._schema_render_pending: list[dict[str, Any]] | None = None
        self._schema_render_timer = QTimer(self)
        self._schema_render_timer.setSingleShot(True)
        self._schema_render_timer.setInterval(250)
        self._schema_render_timer.timeout.connect(self._flush_schema_render)
        self._build_progress = QFrame()
        self._build_progress.setObjectName("schemaBuildProgress")
        self._build_progress.setStyleSheet(
            f"""
            QFrame#schemaBuildProgress {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 8px;
            }}
            """
        )
        progress_layout = QVBoxLayout(self._build_progress)
        progress_layout.setContentsMargins(8, 7, 8, 7)
        progress_layout.setSpacing(4)
        progress_head = QHBoxLayout()
        progress_head.setContentsMargins(0, 0, 0, 0)
        progress_head.setSpacing(8)
        self._build_progress_spinner = QLabel()
        self._build_progress_spinner.setFixedSize(15, 15)
        self._build_progress_spinner.setScaledContents(True)
        progress_head.addWidget(self._build_progress_spinner, 0, Qt.AlignmentFlag.AlignTop)
        self._build_progress_title = QLabel("")
        self._build_progress_title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 12px; font-weight: 650;")
        self._build_progress_title.setWordWrap(True)
        progress_head.addWidget(self._build_progress_title, 1)
        self._build_progress_count = QLabel("")
        self._build_progress_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._build_progress_count.setStyleSheet(f"color: {Theme.TEXT_2}; font-size: 11px; font-weight: 650;")
        progress_head.addWidget(self._build_progress_count)
        progress_layout.addLayout(progress_head)
        self._build_progress_detail = QLabel("")
        self._build_progress_detail.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 11px; margin-left: 23px;"
        )
        self._build_progress_detail.setWordWrap(True)
        progress_layout.addWidget(self._build_progress_detail)
        self._build_progress.hide()
        schema_layout.addWidget(self._build_progress)

        self._asset_state = QFrame()
        self._asset_state.setObjectName("schemaAssetState")
        self._asset_state.setStyleSheet(
            f"""
            QFrame#schemaAssetState {{
                background: transparent;
                border: none;
            }}
            """
        )
        asset_state_layout = QHBoxLayout(self._asset_state)
        asset_state_layout.setContentsMargins(1, 0, 1, 2)
        asset_state_layout.setSpacing(7)
        self._asset_state_icon = QLabel()
        self._asset_state_icon.setFixedSize(14, 14)
        self._asset_state_icon.setScaledContents(True)
        asset_state_layout.addWidget(self._asset_state_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._asset_state_title = QLabel("")
        self._asset_state_title.setStyleSheet(f"color: {Theme.TEXT_2}; font-size: 11px; font-weight: 650;")
        asset_state_layout.addWidget(self._asset_state_title, 0, Qt.AlignmentFlag.AlignVCenter)
        self._asset_state_detail = QLabel("")
        self._asset_state_detail.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        self._asset_state_detail.setTextFormat(Qt.TextFormat.PlainText)
        asset_state_layout.addWidget(self._asset_state_detail, 1, Qt.AlignmentFlag.AlignVCenter)
        self._asset_state.hide()
        schema_layout.addWidget(self._asset_state)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # Column 0 = the schema name (stretches); column 1 = per-row overflow menu.
        self.tree.setColumnCount(2)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 26)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        # Borderless tree that blends into the sidebar — interactivity comes from the
        # row hover/selection (global style), not a boxed frame.
        self.tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        # Single click on a row opens its data (the common case); double-click does the
        # same. Doc is available from the table document's sub-tab or the overflow menu.
        self.tree.itemClicked.connect(self._row_activated)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemExpanded.connect(self._on_tree_expanded)
        self.tree.itemCollapsed.connect(self._on_tree_collapsed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        schema_layout.addWidget(self.tree, 1)
        split.addWidget(schema_panel)

        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        layout.addWidget(split, 1)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {Theme.BORDER_SOFT};")
        layout.addWidget(divider)

        # Full-width ghost footer action (settings entry point).
        self.settings_btn = compact_button(t("topbar.settings"))
        self.settings_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.settings_btn.setMaximumWidth(16777215)
        self.settings_btn.setIcon(svg_icon("settings", color=Theme.MUTED, size=15))
        self.settings_btn.setIconSize(QSize(15, 15))
        self.settings_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Theme.MUTED};"
            f" border: none; border-radius: 8px; text-align: left; padding: 0 8px; }}"
            f"QPushButton:hover {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}"
        )
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_btn)
        self._rows: list[dict[str, Any]] = []
        self._expanded_paths: set[str] = set()
        self._schema_loading_item: QTreeWidgetItem | None = None
        self._schema_busy = BusyAnimator(self._tick_schema_loading, parent=self)
        self._build_progress_busy = BusyAnimator(self._tick_build_progress_spinner, parent=self)
        self._node_refreshing: set[str] = set()
        self._node_busy_buttons: dict[str, QToolButton] = {}
        self._node_busy = BusyAnimator(self._tick_node_refreshing, parent=self)
        self._app_mode = "Assistant"
        self.set_mode("Assistant")

    def set_mode(self, mode: str) -> None:
        """Show the sidebar surface that belongs to the active app mode."""
        self._app_mode = str(mode)
        if self._app_mode == "Workbench":
            self.context_tabs.hide()
            self._show_sidebar_page("Schema")
        else:
            self.context_tabs.show()
            self._sync_context_tab()

    def _on_context_tab_changed(self, _index: int) -> None:
        if self._app_mode != "Workbench":
            self._sync_context_tab()

    def _sync_context_tab(self) -> None:
        self._show_sidebar_page("Schema" if self.context_tabs.currentIndex() == 1 else "Chats")

    def _show_sidebar_page(self, page: str) -> None:
        schema = page == "Schema"
        self.chats.setVisible(not schema)
        self._schema_panel.setVisible(schema)
        self._split.setStretchFactor(0, 0 if schema else 1)
        self._split.setStretchFactor(1, 1 if schema else 0)

    def set_loading(self, message: str = "") -> None:
        """Show a non-blocking placeholder while the schema is being loaded/projected,
        so the panel never sits silently empty during a (possibly slow) fetch."""
        if self._build_progress_active:
            return
        from dbaide.i18n import t as _t
        self._stop_schema_loading()
        self.tree.clear()
        item = QTreeWidgetItem([message or _t("schema.loading")])
        item.setForeground(0, QColor(Theme.MUTED))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._schema_loading_item = item
        self.tree.addTopLevelItem(item)
        self._schema_busy.start()

    def update_loading(self, message: str) -> None:
        """Refresh the current schema-loading row without clearing the tree/spinner."""
        if self._build_progress_active:
            return
        text = str(message or "").strip()
        if not text:
            return
        if self._schema_loading_item is None or not self._schema_busy.active:
            self.set_loading(text)
            return
        self._schema_loading_item.setText(0, text)
        self._tick_schema_loading()

    def start_build_progress(self, message: str = "") -> None:
        from dbaide.i18n import t as _t
        self._build_progress_flush.stop()
        self._build_progress_pending = None
        self._schema_render_timer.stop()
        self._schema_render_pending = None
        self._build_progress_token += 1
        self._build_progress_active = True
        self._build_db_totals.clear()
        self._build_db_completed.clear()
        self._build_progress_title.setText(str(message or _t("build.progress_waiting")))
        self._build_progress_count.setText("")
        self._build_progress_detail.setText("")
        self._stop_schema_loading()
        if not self._tree_has_schema_nodes():
            self.tree.clear()
        self._start_build_progress_spinner()
        self._build_progress.show()

    def _start_build_progress_spinner(self) -> None:
        self._tick_build_progress_spinner()
        self._build_progress_busy.start()

    def _stop_build_progress_spinner(self) -> None:
        self._build_progress_busy.stop()
        self._build_progress_spinner.clear()

    def _tick_build_progress_spinner(self) -> None:
        if self._build_progress.isHidden():
            return
        self._build_progress_spinner.setPixmap(
            spinner_pixmap(self._build_progress_busy.angle, color=Theme.BLUE, size=14),
        )

    def update_build_progress(self, message: object) -> None:
        """Coalesce rapid build events so the progress card does not flicker."""
        self._build_progress_pending = message
        if not self._build_progress_flush.isActive():
            self._build_progress_flush.start()

    def _flush_build_progress(self) -> None:
        try:
            message = self._build_progress_pending
            self._build_progress_pending = None
            if message is None:
                return
            self._apply_build_progress(message)
        except Exception:  # noqa: BLE001 — timer slot must never abort the app
            import logging
            logging.getLogger(__name__).exception("build progress flush failed")

    def _flush_schema_render(self) -> None:
        try:
            rows = self._schema_render_pending
            self._schema_render_pending = None
            if rows is None:
                return
            if self._tree_has_schema_nodes():
                self._sync_schema_tree(rows)
            else:
                self._render(rows)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("schema render flush failed")

    def _apply_build_progress(self, message: object) -> None:
        from dbaide.i18n import t as _t
        if not self._build_progress_active:
            self.start_build_progress(_t("build.progress_waiting"))
        title = ""
        detail = ""
        if isinstance(message, dict):
            title = str(message.get("title") or "").strip()
            node_id = str(message.get("node_id") or "")
            database = str(message.get("database") or "").strip()
            if not database and node_id.startswith("build:db:"):
                database = node_id.removeprefix("build:db:")
            total = _as_int(message.get("total_tables"))
            completed = _as_int(message.get("completed_tables"))
            if database and total is not None:
                self._build_db_totals[database] = total
            if database and completed is not None:
                self._build_db_completed[database] = completed
            current = str(message.get("current_table") or "").strip()
            if current:
                detail = _t("build.progress_current", table=current)
            elif title:
                detail = _t_localized_build_title(title)
        else:
            title = str(message or "").strip()
            if title:
                detail = _t_localized_build_title(title)
        # Headline stays stable after start_build_progress(); live detail goes below.
        if detail and detail != self._build_progress_detail.text():
            self._build_progress_detail.setText(detail)
        total_tables = sum(self._build_db_totals.values())
        completed_tables = sum(self._build_db_completed.values())
        if total_tables > 0:
            count_text = _t("build.progress_tables", done=completed_tables, total=total_tables)
            if self._build_progress_count.text() != count_text:
                self._build_progress_count.setText(count_text)
        elif self._build_progress_count.text():
            self._build_progress_count.setText("")

    def finish_build_progress(self, message: str = "", *, failed: bool = False) -> None:
        from dbaide.i18n import t as _t
        self._build_progress_flush.stop()
        self._build_progress_pending = None
        self._schema_render_timer.stop()
        pending_rows = self._schema_render_pending
        self._schema_render_pending = None
        if pending_rows is not None:
            self._render(pending_rows)
        self._build_progress_token += 1
        token = self._build_progress_token
        if not self._build_progress.isVisible():
            self._build_progress.show()
        if message:
            self._build_progress_title.setText(str(message))
        else:
            self._build_progress_title.setText(
                _t("build.progress_failed_short") if failed else _t("build.progress_complete")
            )
        if failed:
            self._build_progress_detail.setText("")
            self._build_progress_count.setText(_t("build.progress_failed_short"))
            self._stop_build_progress_spinner()
            QTimer.singleShot(5000, lambda token=token: self._hide_build_progress_if_current(token))
            return
        total_tables = sum(self._build_db_totals.values())
        completed_tables = sum(self._build_db_completed.values())
        if total_tables > 0:
            self._build_progress_count.setText(
                _t("build.progress_tables", done=max(completed_tables, total_tables), total=total_tables)
            )
        else:
            self._build_progress_count.setText("")
        self._stop_build_progress_spinner()
        QTimer.singleShot(5000, lambda token=token: self._hide_build_progress_if_current(token))

    def _hide_build_progress_if_current(self, token: int) -> None:
        if token == self._build_progress_token:
            self._stop_build_progress_spinner()
            self._build_progress.hide()
            self._build_progress_active = False

    def _tick_schema_loading(self) -> None:
        if self._schema_loading_item is None:
            return
        try:
            self._schema_loading_item.setIcon(
                0,
                spinner_icon(self._schema_busy.angle, color=Theme.BLUE, size=14),
            )
        except RuntimeError:
            self._stop_schema_loading()

    def _stop_schema_loading(self) -> None:
        self._schema_busy.stop()
        self._schema_loading_item = None

    def set_node_refreshing(self, node_or_path: dict[str, Any] | str, refreshing: bool) -> None:
        """Mark a database/table row as refreshing and swap its overflow icon for a spinner."""
        path = (
            str(node_or_path.get("path") or "")
            if isinstance(node_or_path, dict)
            else str(node_or_path or "")
        ).strip()
        if not path:
            return
        if refreshing:
            self._node_refreshing.add(path)
        else:
            self._node_refreshing.discard(path)
        self._sync_node_refreshing()

    def _sync_node_refreshing(self) -> None:
        if self._node_refreshing:
            self._node_busy.start()
        else:
            self._node_busy.stop()
        self._attach_row_actions()

    def _tick_node_refreshing(self) -> None:
        for btn in list(self._node_busy_buttons.values()):
            btn.setIcon(spinner_icon(self._node_busy.angle, color=Theme.BLUE, size=13))

    def load_schema(self, rows: list[dict[str, Any]], *, error: str = "") -> None:
        self._rows = rows
        if error:
            self.set_asset_summary({"state": "failed", "errors": 1, "message": error})
            self._schema_render_timer.stop()
            self._schema_render_pending = None
            self._stop_schema_loading()
            self.tree.clear()
            from dbaide.i18n import t as _t
            self.tree.addTopLevelItem(QTreeWidgetItem([_t("schema.load_failed", error=error)]))
            return
        self.set_asset_summary(self._asset_summary_from_rows(rows))
        if self._build_progress_active and self._tree_has_schema_nodes():
            self._schema_render_pending = list(rows)
            if not self._schema_render_timer.isActive():
                self._schema_render_timer.start()
            return
        self._render(rows)

    def _tree_has_schema_nodes(self) -> bool:
        item = self.tree.topLevelItem(0)
        if item is None:
            return False
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return isinstance(data, dict) and bool(data.get("kind"))

    def _with_tree_updates(self, fn) -> None:
        self.tree.setUpdatesEnabled(False)
        try:
            fn()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _render(self, rows: list[dict[str, Any]]) -> None:
        from dbaide.i18n import t as _t

        def build() -> None:
            self._stop_schema_loading()
            self._capture_expanded_paths()
            self.tree.clear()
            if not rows:
                item = QTreeWidgetItem([_t("schema.no_assets")])
                item.setToolTip(0, _t("schema.no_assets_hint"))
                item.setForeground(0, QColor(Theme.MUTED))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(item)
                return
            db_icon = svg_icon("database", size=14)
            tbl_icon = svg_icon("table", size=14)
            col_icon = svg_icon("columns", size=14)
            for db in rows:
                db_item = self._make_tree_item(db, db_icon, _t)
                for table in db.get("children", []):
                    table_item = self._make_tree_item(table, tbl_icon, _t, parent_kind="table")
                    db_item.addChild(table_item)
                    for col in table.get("children", []):
                        table_item.addChild(self._make_tree_item(col, col_icon, _t, parent_kind="column"))
                self.tree.addTopLevelItem(db_item)
            self._attach_row_actions()
            self._restore_expanded_paths()

        self._with_tree_updates(build)

    def _sync_schema_tree(self, rows: list[dict[str, Any]]) -> None:
        from dbaide.i18n import t as _t

        def sync() -> None:
            self._stop_schema_loading()
            self._capture_expanded_paths()
            existing: dict[str, QTreeWidgetItem] = {}
            for item in self._iter_tree_items():
                path = self._item_path(item)
                if path:
                    existing[path] = item
            seen: set[str] = set()

            def walk(data: dict[str, Any], parent: QTreeWidgetItem | None, icon_kind: str) -> None:
                path = str(data.get("path") or "").strip()
                if path:
                    seen.add(path)
                item = existing.get(path) if path else None
                if item is None:
                    icons = {
                        "database": svg_icon("database", size=14),
                        "table": svg_icon("table", size=14),
                        "column": svg_icon("columns", size=14),
                    }
                    item = self._make_tree_item(data, icons.get(icon_kind, svg_icon("table", size=14)), _t,
                                                parent_kind=icon_kind)
                    if parent is None:
                        self.tree.addTopLevelItem(item)
                    else:
                        parent.addChild(item)
                    if path:
                        existing[path] = item
                    self._attach_item_actions(item, data)
                else:
                    self._update_tree_item(item, data, _t)
                child_kind = "table" if icon_kind == "database" else "column"
                for child in data.get("children", []):
                    walk(child, item, child_kind)

            for db in rows:
                walk(db, None, "database")

            for path, item in sorted(
                ((p, i) for p, i in existing.items() if p not in seen),
                key=lambda pair: pair[0].count("."),
                reverse=True,
            ):
                try:
                    self.tree.removeItemWidget(item, 1)
                except RuntimeError:
                    pass
                parent = item.parent()
                if parent is not None:
                    parent.removeChild(item)
                else:
                    index = self.tree.indexOfTopLevelItem(item)
                    if index >= 0:
                        self.tree.takeTopLevelItem(index)

            self._restore_expanded_paths()

        self._with_tree_updates(sync)

    def _make_tree_item(
        self,
        data: dict[str, Any],
        default_icon,
        _t,
        *,
        parent_kind: str = "database",
    ) -> QTreeWidgetItem:
        kind = str(data.get("kind") or parent_kind)
        if kind == "table":
            label = f"{data['name']} ({data.get('column_count', 0)})"
        elif kind == "column":
            suffix = f" · {data.get('data_type')}" if data.get("data_type") else ""
            label = f"{data['name']}{suffix}"
        else:
            label = str(data.get("name") or "")
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._style_tree_item(item, data, default_icon, _t)
        return item

    def _update_tree_item(self, item: QTreeWidgetItem, data: dict[str, Any], _t) -> None:
        kind = str(data.get("kind") or "")
        if kind == "table":
            label = f"{data['name']} ({data.get('column_count', 0)})"
        elif kind == "column":
            suffix = f" · {data.get('data_type')}" if data.get("data_type") else ""
            label = f"{data['name']}{suffix}"
        else:
            label = str(data.get("name") or "")
        if item.text(0) != label:
            item.setText(0, label)
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        default_icon = svg_icon(
            {"database": "database", "table": "table", "column": "columns"}.get(kind, "table"),
            size=14,
        )
        self._style_tree_item(item, data, default_icon, _t)

    def _style_tree_item(self, item: QTreeWidgetItem, data: dict[str, Any], default_icon, _t) -> None:
        kind = str(data.get("kind") or "")
        if kind == "table":
            stale = bool(data.get("stale"))
            enriched = bool(data.get("enriched"))
            if stale:
                item.setIcon(0, svg_icon("alert-triangle", color=Theme.YELLOW, size=14))
                item.setToolTip(0, _t("schema.status_stale"))
            else:
                item.setIcon(0, default_icon)
                if enriched:
                    item.setToolTip(0, _t("schema.status_enriched"))
                    item.setForeground(0, QColor(Theme.TEXT))
                else:
                    item.setForeground(0, QColor(Theme.MUTED_2))
                    item.setToolTip(0, _t("schema.status_base"))
        else:
            item.setIcon(0, default_icon)
            item.setForeground(0, QColor(Theme.TEXT))
            item.setToolTip(0, "")

    def _attach_item_actions(self, item: QTreeWidgetItem, data: dict[str, Any]) -> None:
        from dbaide.i18n import t
        if data.get("kind") in ("database", "table", "column"):
            self.tree.setItemWidget(item, 1, self._row_actions(data, t))

    def _item_path(self, item: QTreeWidgetItem) -> str:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            return str(data.get("path") or "").strip()
        return ""

    def _iter_tree_items(self):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is None:
                continue
            stack = [item]
            while stack:
                current = stack.pop()
                yield current
                for child_index in range(current.childCount()):
                    child = current.child(child_index)
                    if child is not None:
                        stack.append(child)

    def _capture_expanded_paths(self) -> None:
        for item in self._iter_tree_items():
            if not item.isExpanded():
                continue
            path = self._item_path(item)
            if path:
                self._expanded_paths.add(path)

    def _restore_expanded_paths(self) -> None:
        if not self._expanded_paths:
            return
        self.tree.blockSignals(True)
        try:
            for item in self._iter_tree_items():
                path = self._item_path(item)
                if path and path in self._expanded_paths:
                    item.setExpanded(True)
        finally:
            self.tree.blockSignals(False)

    def _on_tree_expanded(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        if path:
            self._expanded_paths.add(path)

    def _on_tree_collapsed(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        if path:
            self._expanded_paths.discard(path)

    def clear_schema_expansion(self) -> None:
        """Reset manual expand/collapse state (e.g. when switching connections)."""
        self._expanded_paths.clear()

    def reset_live_updates(self) -> None:
        """Drop debounced schema/build UI work when the connection context changes."""
        self._build_progress_flush.stop()
        self._build_progress_pending = None
        self._schema_render_timer.stop()
        self._schema_render_pending = None
        self._build_progress_token += 1
        self._build_progress_active = False
        self._stop_build_progress_spinner()
        self._build_progress.hide()

    def set_asset_summary(self, summary: dict[str, Any] | None) -> None:
        from dbaide.i18n import t as _t
        summary = summary or {}
        state = str(summary.get("state") or "missing")
        color = {
            "sampled": Theme.GREEN,
            "partial": Theme.BLUE,
            "base": Theme.TEXT_2,
            "stale": Theme.YELLOW,
            "failed": Theme.RED,
            "missing": Theme.MUTED,
        }.get(state, Theme.MUTED)
        icon = {
            "sampled": "check",
            "partial": "database",
            "base": "database",
            "stale": "alert-triangle",
            "failed": "alert-triangle",
            "missing": "database",
        }.get(state, "database")
        self._asset_state_icon.setPixmap(svg_pixmap(icon, color=color, size=14, width=1.8))
        self._asset_state_title.setText(_t(f"schema.asset_state.{state}"))
        if summary.get("message"):
            detail = str(summary.get("message") or "")
        else:
            errors = int(summary.get("errors") or 0)
            detail = _t(
                "schema.asset_state.detail",
                tables=int(summary.get("tables") or 0),
                columns=int(summary.get("columns") or 0),
                sampled=int(summary.get("sampled_tables") or 0),
            )
            if errors:
                detail += _t("schema.asset_state.errors", errors=errors)
        self._asset_state_detail.setText(detail)
        self._asset_state.setToolTip(detail)
        self._asset_state.show()

    def _asset_summary_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        for row in rows or []:
            summary = row.get("asset_summary")
            if isinstance(summary, dict):
                return summary
        if not rows:
            return {"state": "missing", "tables": 0, "columns": 0, "sampled_tables": 0, "errors": 0}
        tables = 0
        columns = 0
        sampled = 0
        stale = 0
        for db in rows:
            for table in db.get("children", []) or []:
                tables += 1
                columns += int(table.get("column_count") or len(table.get("children") or []))
                state = str(table.get("asset_state") or "")
                if state == "stale" or table.get("stale"):
                    stale += 1
                if state == "sampled" or table.get("enriched"):
                    sampled += 1
        errors = 0
        for row in rows or []:
            summary = row.get("asset_summary")
            if isinstance(summary, dict):
                errors = int(summary.get("errors") or 0)
                break
        if errors:
            state = "failed"
        elif stale:
            state = "stale"
        elif sampled == tables and tables:
            state = "sampled"
        elif sampled:
            state = "partial"
        else:
            state = "base"
        return {
            "state": state,
            "tables": tables,
            "columns": columns,
            "sampled_tables": sampled,
            "stale_tables": stale,
            "errors": errors,
            "profile_state": "on_demand",
        }

    def _attach_row_actions(self) -> None:
        from dbaide.i18n import t
        self._node_busy_buttons.clear()
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") in ("database", "table", "column"):
                self.tree.setItemWidget(item, 1, self._row_actions(data, t))
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _row_actions(self, data: dict[str, Any], t) -> QWidget:
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 2, 0)
        lay.setSpacing(2)
        lay.addStretch(1)
        lay.addWidget(self._more_button(data, t))
        return holder

    def _more_button(self, data: dict[str, Any], t) -> QToolButton:
        from PyQt6.QtWidgets import QApplication, QMenu
        from dbaide.desktop.components.menu import _style_menu

        path = str(data.get("path") or "")
        btn = self._button_base("more-horizontal", t("schema.more"))
        if path in self._node_refreshing:
            btn.setIcon(spinner_icon(self._node_busy.angle, color=Theme.BLUE, size=13))
            btn.setToolTip(t("status.syncing"))
            btn.setEnabled(False)
            self._node_busy_buttons[path] = btn
            return btn
        kind = data.get("kind")
        menu = QMenu(btn)
        _style_menu(menu)
        if kind == "table":
            menu.addAction(t("schema.open_data"), lambda d=data: self.schema_selected.emit(d))
        if kind in ("database", "table") and data.get("path"):
            menu.addAction(t("schema.view_doc"), lambda d=data: self.schema_preview.emit(d))
        if kind in ("database", "table"):
            menu.addAction(t("schema.enrich"), lambda d=data: self.enrich_requested.emit(d))
        menu.addAction(t("schema.edit_note"), lambda d=data: self.edit_note.emit(d))
        if kind in ("database", "table"):
            menu.addAction(t("schema.refresh_node"), lambda d=data: self.refresh_requested.emit(d))
        if kind == "table":
            menu.addAction(t("schema.backup_table"), lambda d=data: self.backup_requested.emit(d))
        elif kind == "database":
            menu.addAction(t("schema.backup_database"), lambda d=data: self.backup_requested.emit(d))
        if kind == "table":
            gen = menu.addMenu(t("schema.generate_sql"))
            _style_menu(gen)
            for gkind, key in (
                ("select_star", "schema.gen_select_star"),
                ("select_columns", "schema.gen_select_columns"),
                ("count", "schema.gen_count"),
                ("insert", "schema.gen_insert"),
                ("update", "schema.gen_update"),
            ):
                gen.addAction(t(key), lambda _c=False, k=gkind, d=data: self.generate_sql.emit(d, k))
        menu.addSeparator()
        name = str(data.get("name") or "")
        menu.addAction(t("schema.copy_name"),
                       lambda: QApplication.clipboard().setText(name))
        if path:
            qualified = ".".join(path.split(".")[1:]) or name
            menu.addAction(t("schema.copy_qualified"),
                           lambda: QApplication.clipboard().setText(qualified))
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return btn

    def _button_base(self, icon: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        # TEXT_2 (not MUTED) so the icon is clearly visible at rest — a muted-grey
        # stroke icon at the row's edge reads as "nothing there". Brightens on hover.
        # Small size + thin (1.4px) stroke so the row-edge icons stay unobtrusive.
        if icon == "more-horizontal":
            btn.setIcon(more_icon(color=Theme.TEXT_2, size=13))
            btn.setIconSize(QSize(13, 13))
        else:
            btn.setIcon(svg_icon(icon, color=Theme.TEXT_2, size=13, width=1.4))
            btn.setIconSize(QSize(13, 13))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(18, 18)
        # Override the GLOBAL QToolButton rule (padding:0 10px; min/max-height:26px;
        # border) — otherwise the side padding squeezes the icon down to a dot.
        btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 4px;"
            f" padding: 0; margin: 0; min-width: 0; max-width: 18px;"
            f" min-height: 0; max-height: 18px; color: {Theme.TEXT_2}; }}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
            f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
        )
        return btn

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._render(self._rows)
            return
        filtered: list[dict[str, Any]] = []
        for db in self._rows:
            # A match on the DATABASE name shows the whole database (all its tables) —
            # otherwise filtering by a db name returned nothing unless a table/column
            # also happened to contain the needle.
            if needle in str(db.get("name") or "").lower():
                filtered.append(dict(db))
                continue
            db_copy = dict(db)
            db_copy["children"] = []
            for table in db.get("children", []):
                table_copy = dict(table)
                table_copy["children"] = []
                if needle in table["name"].lower():
                    table_copy["children"] = list(table.get("children", []))
                    db_copy["children"].append(table_copy)
                    continue
                for col in table.get("children", []):
                    if needle in col["name"].lower():
                        table_copy["children"].append(col)
                if table_copy["children"]:
                    db_copy["children"].append(table_copy)
            if db_copy["children"]:
                filtered.append(db_copy)
        self._render(filtered)

    def _semantic_search(self) -> None:
        query = self.search.text().strip()
        if query:
            self.semantic_search_requested.emit(query)

    def _row_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        # Single click opens the row's data (tables → data browser; db/column fall back
        # to their doc inside open_schema_asset).
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("path"):
            self.schema_selected.emit(data)

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        # Same as a single click (kept for muscle memory).
        self._row_activated(item, _column)




def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _t_localized_build_title(title: str) -> str:
    from dbaide.i18n import localized_build_title
    return localized_build_title(title)
