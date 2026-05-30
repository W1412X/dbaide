from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from dbaide.desktop.components.markdown import MarkdownView
from dbaide.desktop.theme import Theme


class AssetsTab(QWidget):
    asset_selected = pyqtSignal(str)
    search_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search offline assets…")
        self.search.returnPressed.connect(self._search)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._select)
        self.preview = MarkdownView()
        self.preview.setStyleSheet(f"QTextBrowser {{ background: {Theme.SURFACE}; border: 1px solid {Theme.BORDER_SOFT}; }}")
        splitter.addWidget(self.tree)
        splitter.addWidget(self.preview)
        splitter.setSizes([260, 540])
        layout.addWidget(splitter, 1)
        self._rows: list[dict[str, Any]] = []

    def load_schema(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.tree.clear()
        for db in rows:
            db_item = QTreeWidgetItem([db["name"]])
            db_item.setData(0, Qt.ItemDataRole.UserRole, db.get("path"))
            for table in db.get("children", []):
                table_item = QTreeWidgetItem([table["name"]])
                table_item.setData(0, Qt.ItemDataRole.UserRole, table.get("path"))
                db_item.addChild(table_item)
                for col in table.get("children", []):
                    col_item = QTreeWidgetItem([col["name"]])
                    col_item.setData(0, Qt.ItemDataRole.UserRole, col.get("path"))
                    table_item.addChild(col_item)
            self.tree.addTopLevelItem(db_item)
            db_item.setExpanded(True)

    def show_markdown(self, markdown: str, title: str = "Asset Preview") -> None:
        self.preview.clear_view()
        self.preview.append_card(title, markdown)

    def _select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        path = items[0].data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.asset_selected.emit(str(path))

    def show_search_hits(self, query: str, hits: list[dict[str, Any]]) -> None:
        self.preview.clear_view()
        if not hits:
            self.preview.append_card(
                "Asset search",
                f"No matches for `{query}`. Build assets or ask in natural language on the Ask tab.",
            )
            return
        lines = [f"Found **{len(hits)}** matches for `{query}`:", ""]
        for hit in hits:
            lines.append(f"- **{hit.get('path')}** ({hit.get('kind')}, score {hit.get('score', 0):.1f})")
            if hit.get("summary"):
                lines.append(f"  {str(hit['summary'])[:160]}")
        self.preview.append_card("Asset search", "\n".join(lines))

    def _search(self) -> None:
        query = self.search.text().strip()
        if query:
            self.search_requested.emit(query)
