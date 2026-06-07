"""Table structure view — columns grid + relations + generated DDL (DBeaver-style).

Renders from the schema asset already in memory (the columns and foreign-key data
carried by the tree node), so opening it is instant — no extra database
round-trip. Columns show name/type/key; the Relations section lists outgoing and
incoming foreign keys with the related table as a clickable link (``navigate_table``);
a generated CREATE TABLE skeleton is shown below.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.empty_state import EmptyState
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.sql_highlighter import SqlHighlighter
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme


def _generate_ddl(table: str, columns: list[dict[str, Any]]) -> str:
    if not columns:
        return f"-- {table}: no column metadata"
    defs = []
    for c in columns:
        line = f"  {c.get('name', '')} {c.get('data_type') or ''}".rstrip()
        if c.get("primary_key"):
            line += " PRIMARY KEY"
        defs.append(line)
    return f"CREATE TABLE {table} (\n" + ",\n".join(defs) + "\n);"


class StructurePanel(QWidget):
    navigate_table = pyqtSignal(str)  # a related table name was clicked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.stack = QStackedWidget()

        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.setContentsMargins(0, 0, 0, 0)
        el.addStretch(1)
        self._empty = EmptyState(
            t("structure.empty_title"),
            t("structure.empty_hint"),
            icon="columns",
        )
        el.addWidget(self._empty)
        el.addStretch(1)
        self.stack.addWidget(empty_page)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(16, 10, 16, 0)
        pl.setSpacing(10)
        self._title = QLabel("")
        self._title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        pl.addWidget(self._title)
        self._cols = ResultTableWidget()
        self._cols.meta.setVisible(False)
        self._cols.set_toolbar_visible(False)  # no value-viewer/export for a schema list
        pl.addWidget(self._cols, 1)

        # Relations — outgoing/incoming foreign keys, with clickable related tables.
        self._relations = QLabel("")
        self._relations.setTextFormat(Qt.TextFormat.RichText)
        self._relations.setWordWrap(True)
        self._relations.setOpenExternalLinks(False)
        self._relations.setStyleSheet(
            f"QLabel {{ color: {Theme.TEXT_2}; font-size: 12px; }}"
            f"a {{ color: {Theme.BLUE}; text-decoration: none; }}"
        )
        self._relations.linkActivated.connect(self._on_link)
        pl.addWidget(self._relations)

        # Indexes — name (columns) [UNIQUE], offline from the schema asset.
        self._indexes = QLabel("")
        self._indexes.setTextFormat(Qt.TextFormat.RichText)
        self._indexes.setWordWrap(True)
        self._indexes.setStyleSheet(f"QLabel {{ color: {Theme.TEXT_2}; font-size: 12px; }}")
        pl.addWidget(self._indexes)

        ddl_row = QHBoxLayout()
        ddl_row.setContentsMargins(0, 0, 0, 0)
        self._ddl_label = QLabel(t("structure.ddl"))
        self._ddl_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600;")
        ddl_row.addWidget(self._ddl_label)
        ddl_row.addStretch(1)
        self._copy_ddl = QToolButton()
        self._copy_ddl.setIcon(svg_icon("copy", color=Theme.TEXT_2, size=13))
        self._copy_ddl.setToolTip(t("structure.copy_ddl"))
        self._copy_ddl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_ddl.setFixedSize(24, 24)
        self._copy_ddl.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
        )
        self._copy_ddl.clicked.connect(self._on_copy_ddl)
        ddl_row.addWidget(self._copy_ddl)
        pl.addLayout(ddl_row)
        self._ddl = QPlainTextEdit()
        self._ddl.setReadOnly(True)
        self._ddl.setFont(QFont("Menlo", 11))
        self._ddl.setFixedHeight(150)
        self._ddl.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.CODE_BG}; border: 1px solid {Theme.BORDER_SOFT};"
            f" border-radius: 8px; }}"
        )
        self._ddl_hl = SqlHighlighter(self._ddl.document())
        pl.addWidget(self._ddl)
        self.stack.addWidget(page)
        outer.addWidget(self.stack)

    def show_table(
        self,
        table: str,
        columns: list[dict[str, Any]],
        relations: dict[str, list[dict[str, Any]]] | None = None,
        indexes: list[dict[str, Any]] | None = None,
    ) -> None:
        self._title.setText(table)
        rows = [{
            "Column": c.get("name", ""),
            "Type": c.get("data_type") or "",
            "Key": "PK" if c.get("primary_key") else ("indexed" if c.get("indexed") else " "),
        } for c in (columns or [])]
        self._cols.load(columns=["Column", "Type", "Key"], rows=rows, row_count=len(rows))
        self._relations.setText(self._relations_html(relations or {}))
        self._indexes.setText(self._indexes_text(indexes or []))
        # Show a generated skeleton instantly; the real DDL from the database replaces
        # it via set_ddl() once the (lazy) fetch returns.
        self._ddl.setPlainText(_generate_ddl(table, columns or []))
        self._ddl_label.setText(self._t("structure.ddl"))
        self.stack.setCurrentIndex(1)

    def set_ddl(self, ddl: str) -> None:
        """Replace the generated skeleton with the database's real CREATE TABLE DDL."""
        ddl = (ddl or "").strip()
        if not ddl:
            return
        self._ddl.setPlainText(ddl)
        self._ddl_label.setText(self._t("structure.ddl_real"))

    def _indexes_text(self, indexes: list[dict[str, Any]]) -> str:
        # Skip the primary-key index (already shown in the Key column).
        items = []
        for ix in indexes:
            if ix.get("primary"):
                continue
            cols = ", ".join(ix.get("columns") or [])
            label = f"{ix.get('name', '')} ({cols})"
            if ix.get("unique"):
                label += " UNIQUE"
            items.append(label)
        if not items:
            return ""
        return f"<b>{self._t('structure.indexes')}</b> " + ",  ".join(items)

    # ── relations rendering ──────────────────────────────────────────────────--

    def _relations_html(self, relations: dict[str, list[dict[str, Any]]]) -> str:
        t = self._t
        outgoing = relations.get("foreign_keys") or []
        incoming = relations.get("referenced_by") or []
        if not outgoing and not incoming:
            return ""
        parts: list[str] = []
        if outgoing:
            items = ", ".join(
                f"{fk.get('column', '')} → {self._link(fk.get('ref_table', ''))}.{fk.get('ref_column', '')}"
                for fk in outgoing
            )
            parts.append(f"<b>{t('structure.references')}</b> {items}")
        if incoming:
            items = ", ".join(
                f"{self._link(fk.get('table', ''))}.{fk.get('column', '')}"
                for fk in incoming
            )
            parts.append(f"<b>{t('structure.referenced_by')}</b> {items}")
        return "<br>".join(parts)

    @staticmethod
    def _link(table: str) -> str:
        table = str(table or "")
        if not table:
            return ""
        return f'<a href="{table}">{table}</a>'

    def _on_link(self, href: str) -> None:
        if href:
            self.navigate_table.emit(href)

    def _on_copy_ddl(self) -> None:
        QApplication.clipboard().setText(self._ddl.toPlainText())
