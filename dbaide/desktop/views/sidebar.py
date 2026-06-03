from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLineEdit,
    QSizePolicy,
    QSplitter,
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
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        # Borderless tree that blends into the sidebar — interactivity comes from the
        # row hover/selection (global style), not a boxed frame.
        self.tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        self.tree.itemSelectionChanged.connect(self._selection_changed)
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

    def load_schema(self, rows: list[dict[str, Any]], *, error: str = "") -> None:
        self._rows = rows
        if error:
            self.tree.clear()
            self.tree.addTopLevelItem(QTreeWidgetItem([f"Schema load failed: {error}"]))
            return
        self._render(rows)

    def _render(self, rows: list[dict[str, Any]]) -> None:
        self.tree.clear()
        if not rows:
            from dbaide.desktop.theme import Theme
            from PyQt6.QtGui import QColor
            item = QTreeWidgetItem(["No assets yet"])
            item.setToolTip(0, "Build assets from the ⋯ menu (top-right) for richer answers.")
            item.setForeground(0, QColor(Theme.MUTED))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(item)
            return
        for db in rows:
            db_item = QTreeWidgetItem([db["name"]])
            db_item.setData(0, Qt.ItemDataRole.UserRole, db)
            for table in db.get("children", []):
                table_item = QTreeWidgetItem([f"{table['name']} ({table.get('column_count', 0)})"])
                table_item.setData(0, Qt.ItemDataRole.UserRole, table)
                db_item.addChild(table_item)
                for col in table.get("children", []):
                    suffix = f" · {col.get('data_type')}" if col.get("data_type") else ""
                    col_item = QTreeWidgetItem([f"{col['name']}{suffix}"])
                    col_item.setData(0, Qt.ItemDataRole.UserRole, col)
                    table_item.addChild(col_item)
            self.tree.addTopLevelItem(db_item)
            db_item.setExpanded(True)

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

    def _selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("path"):
            self.schema_preview.emit(data)

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("path"):
            self.schema_selected.emit(data)

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
