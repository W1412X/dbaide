from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from PyQt6.QtWidgets import QLabel

from dbaide.desktop.components.base import SectionLabel, compact_button
from dbaide.desktop.components.icons import svg_icon, svg_pixmap
from dbaide.desktop.components.session_list import SessionList
from dbaide.desktop.theme import Theme


class Sidebar(QWidget):
    schema_preview = pyqtSignal(dict)
    schema_selected = pyqtSignal(dict)
    semantic_search_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    generate_sql = pyqtSignal(dict, str)  # (table node, template kind)
    edit_note = pyqtSignal(dict)  # edit the user note for a db/table/column node
    enrich_requested = pyqtSignal(dict)  # build the optional enrichment for a db/table node

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        from dbaide.i18n import t

        # Chats (会话) over Schema, split so each can be resized; both stay visible.
        split = QSplitter(Qt.Orientation.Vertical)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)

        self.chats = SessionList()
        split.addWidget(self.chats)

        schema_panel = QWidget()
        schema_layout = QVBoxLayout(schema_panel)
        schema_layout.setContentsMargins(0, 0, 0, 0)
        schema_layout.setSpacing(8)
        schema_layout.addWidget(SectionLabel("SCHEMA"))
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
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # Column 0 = the schema name (stretches); column 1 = small per-row action
        # icons. Clicking the row opens its DATA; the doc icon opens the DOC; the
        # pencil icon edits the object's user note (db/table/column).
        self.tree.setColumnCount(2)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 42)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        # Borderless tree that blends into the sidebar — interactivity comes from the
        # row hover/selection (global style), not a boxed frame.
        self.tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        # Single click on a row opens its data (the common case); double-click does the
        # same. The per-row doc icon (column 1) opens the offline doc instead.
        self.tree.itemClicked.connect(self._row_activated)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
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

    def set_loading(self, message: str = "") -> None:
        """Show a non-blocking placeholder while the schema is being loaded/projected,
        so the panel never sits silently empty during a (possibly slow) fetch."""
        from dbaide.i18n import t as _t
        self.tree.clear()
        item = QTreeWidgetItem([message or _t("schema.loading")])
        item.setForeground(0, QColor(Theme.MUTED))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(item)

    def load_schema(self, rows: list[dict[str, Any]], *, error: str = "") -> None:
        self._rows = rows
        if error:
            self.tree.clear()
            from dbaide.i18n import t as _t
            self.tree.addTopLevelItem(QTreeWidgetItem([_t("schema.load_failed", error=error)]))
            return
        self._render(rows)

    def _render(self, rows: list[dict[str, Any]]) -> None:
        from dbaide.i18n import t as _t
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
            db_item = QTreeWidgetItem([db["name"]])
            db_item.setIcon(0, db_icon)
            db_item.setData(0, Qt.ItemDataRole.UserRole, db)
            for table in db.get("children", []):
                # Enrichment status: stale → ⚠ + tooltip; base (catalog-only) → dimmed
                # so it reads as "structure only, not yet enriched"; enriched → normal.
                stale = bool(table.get("stale"))
                enriched = bool(table.get("enriched"))
                label = f"{table['name']} ({table.get('column_count', 0)})" + (" ⚠" if stale else "")
                table_item = QTreeWidgetItem([label])
                table_item.setIcon(0, tbl_icon)
                table_item.setData(0, Qt.ItemDataRole.UserRole, table)
                if stale:
                    table_item.setToolTip(0, _t("schema.status_stale"))
                elif enriched:
                    table_item.setToolTip(0, _t("schema.status_enriched"))
                else:
                    table_item.setForeground(0, QColor(Theme.MUTED_2))
                    table_item.setToolTip(0, _t("schema.status_base"))
                db_item.addChild(table_item)
                for col in table.get("children", []):
                    suffix = f" · {col.get('data_type')}" if col.get("data_type") else ""
                    col_item = QTreeWidgetItem([f"{col['name']}{suffix}"])
                    col_item.setIcon(0, col_icon)
                    col_item.setData(0, Qt.ItemDataRole.UserRole, col)
                    table_item.addChild(col_item)
            self.tree.addTopLevelItem(db_item)
            db_item.setExpanded(True)
        # Add per-row action icons. Databases/tables get a "view doc" icon (they have
        # an offline doc) plus a "edit note" pencil; columns get just the pencil (their
        # note shows inside the table's doc). Done after the tree is built so the item
        # widgets attach to live items.
        self._attach_row_actions()

    def _attach_row_actions(self) -> None:
        from dbaide.i18n import t
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
        if data.get("path") and data.get("kind") in ("database", "table"):
            lay.addWidget(self._icon_button(
                "file-text", t("schema.view_doc"),
                lambda d=data: self.schema_preview.emit(d)))
        lay.addWidget(self._icon_button(
            "pencil", t("schema.edit_note"),
            lambda d=data: self.edit_note.emit(d)))
        return holder

    def _icon_button(self, icon: str, tooltip: str, on_click) -> QToolButton:
        btn = QToolButton()
        # TEXT_2 (not MUTED) so the icon is clearly visible at rest — a muted-grey
        # stroke icon at the row's edge reads as "nothing there". Brightens on hover.
        # Small size + thin (1.4px) stroke so the row-edge icons stay unobtrusive.
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
            f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
        )
        btn.clicked.connect(lambda _checked=False: on_click())
        return btn

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._render(self._rows)
            return
        filtered: list[dict[str, Any]] = []
        for db in self._rows:
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
        # to their doc inside open_schema_asset). The doc icon is the explicit doc path.
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("path"):
            self.schema_selected.emit(data)

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        # Same as a single click (kept for muscle memory).
        self._row_activated(item, _column)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not (isinstance(data, dict) and data.get("name")):
            return
        from PyQt6.QtWidgets import QApplication, QMenu
        from dbaide.desktop.components.menu import _style_menu
        from dbaide.i18n import t
        kind = data.get("kind")
        menu = QMenu(self)
        _style_menu(menu)
        if kind == "table":
            menu.addAction(t("schema.open_data"), lambda: self.schema_selected.emit(data))
        if kind in ("database", "table") and data.get("path"):
            menu.addAction(t("schema.view_doc"), lambda: self.schema_preview.emit(data))
        if kind in ("database", "table"):
            menu.addAction(t("schema.enrich"), lambda: self.enrich_requested.emit(data))
        if kind in ("database", "table", "column"):
            menu.addAction(t("schema.edit_note"), lambda: self.edit_note.emit(data))
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
                gen.addAction(t(key), lambda _checked=False, k=gkind: self.generate_sql.emit(data, k))
            menu.addSeparator()
        # Copy name — available for any named node (table, column, database).
        name = str(data.get("name") or "")
        menu.addAction(t("schema.copy_name"),
                       lambda: QApplication.clipboard().setText(name))
        path = str(data.get("path") or "")
        if path:
            # Qualified name = the dotted path minus the connection prefix.
            qualified = ".".join(path.split(".")[1:]) or name
            menu.addAction(t("schema.copy_qualified"),
                           lambda: QApplication.clipboard().setText(qualified))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
