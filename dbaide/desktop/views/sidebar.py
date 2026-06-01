from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from dbaide.desktop.components.base import SectionLabel, compact_button


class Sidebar(QWidget):
    schema_preview = pyqtSignal(dict)
    schema_selected = pyqtSignal(dict)
    semantic_search_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        from dbaide.i18n import t
        layout.addWidget(SectionLabel("SCHEMA"))
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("sidebar.filter"))
        self.search.setFixedHeight(34)
        self.search.textChanged.connect(self._filter_tree)
        self.search.returnPressed.connect(self._semantic_search)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        layout.addWidget(self.tree, 1)
        self.settings_btn = compact_button(t("topbar.settings"), width=120)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
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
