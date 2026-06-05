"""Table structure view — columns grid + relations + generated DDL (DBeaver-style).

Renders from the schema asset already in memory (the columns and foreign-key data
carried by the tree node), so opening it is instant — no extra database
round-trip. Columns show name/type/key and an editable **Note** the user can fill
right in the document (authoritative annotations, surfaced to the assistant). The
table itself also has an inline note field. Edits emit ``note_edited`` upward; the
window persists them. The Relations section lists outgoing/incoming foreign keys
with the related table as a clickable link (``navigate_table``); a generated CREATE
TABLE skeleton is shown below.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.sql_highlighter import SqlHighlighter
from dbaide.desktop.theme import Theme

_NOTE_COL = 3  # index of the editable "Note" column in the structure grid


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


class _StructureGrid(QTableWidget):
    """Columns grid with an inline-editable Note column (only Note is editable)."""

    note_committed = pyqtSignal(str, str)  # (column_name, note_text)

    def __init__(self, parent=None) -> None:
        super().__init__(0, 4, parent)
        from dbaide.i18n import t
        self.setHorizontalHeaderLabels([t("structure.col_column"), t("structure.col_type"),
                                        t("structure.col_key"), t("structure.col_note")])
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.setFont(QFont("Menlo", 11))
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_NOTE_COL, QHeaderView.ResizeMode.Stretch)
        self.setStyleSheet(
            f"""
            QTableWidget {{ background: {Theme.SURFACE}; border: none; outline: none; }}
            QTableWidget::item {{ border-bottom: 1px solid {Theme.BORDER_SOFT}; padding: 4px 10px; }}
            QTableWidget::item:selected {{ background: {Theme.PANEL_3}; color: {Theme.TEXT}; }}
            QHeaderView::section:horizontal {{
                background: {Theme.SURFACE}; color: {Theme.MUTED}; padding: 7px 10px;
                border: none; border-bottom: 1px solid {Theme.BORDER}; font-weight: 600;
            }}
            """
        )
        self._loading = False
        self.itemChanged.connect(self._on_item_changed)

    def set_columns(self, columns: list[dict[str, Any]]) -> None:
        from dbaide.i18n import t
        self._loading = True
        self.setRowCount(0)
        self.setRowCount(len(columns))
        for r, c in enumerate(columns):
            name = str(c.get("name") or "")
            key = "PK" if c.get("primary_key") else ("indexed" if c.get("indexed") else "")
            cells = [name, str(c.get("data_type") or ""), key]
            for col_idx, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 2 and key:
                    item.setForeground(QColor(Theme.MUTED))
                self.setItem(r, col_idx, item)
            note_item = QTableWidgetItem(str(c.get("note") or ""))
            note_item.setFlags(note_item.flags() | Qt.ItemFlag.ItemIsEditable)
            if not c.get("note"):
                note_item.setForeground(QColor(Theme.MUTED_2))
            note_item.setToolTip(t("structure.note_hint"))
            self.setItem(r, _NOTE_COL, note_item)
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != _NOTE_COL:
            return
        name_item = self.item(item.row(), 0)
        if name_item is None:
            return
        text = item.text().strip()
        # Restore default vs. authored colour so empty cells read as placeholders.
        item.setForeground(QColor(Theme.TEXT if text else Theme.MUTED_2))
        self.note_committed.emit(name_item.text().strip(), text)


class StructurePanel(QWidget):
    navigate_table = pyqtSignal(str)        # a related table name was clicked
    note_edited = pyqtSignal(str, str)      # (column_name or "" for table-level, note text)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._table_name = ""
        self._table_note_value = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.stack = QStackedWidget()

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        hint = QLabel(t("structure.empty_hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        el.addWidget(hint)
        el.addStretch(1)
        self.stack.addWidget(empty)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(16, 10, 16, 0)
        pl.setSpacing(10)
        self._title = QLabel("")
        self._title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        pl.addWidget(self._title)

        # Inline table-level note — edited right here in the document.
        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(6)
        tag = QLabel(t("structure.table_note"))
        tag.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600;")
        note_row.addWidget(tag)
        self._table_note = QLineEdit()
        self._table_note.setPlaceholderText(t("structure.table_note_ph"))
        self._table_note.setStyleSheet(
            f"QLineEdit {{ background: {Theme.PANEL}; color: {Theme.TEXT}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 6px; padding: 4px 8px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {Theme.ACCENT}; }}"
        )
        self._table_note.editingFinished.connect(self._on_table_note_done)
        note_row.addWidget(self._table_note, 1)
        pl.addLayout(note_row)

        self._cols = _StructureGrid()
        self._cols.note_committed.connect(self._on_column_note)
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
        table_note: str = "",
    ) -> None:
        self._table_name = table
        self._title.setText(table)
        self._table_note_value = str(table_note or "")
        self._table_note.blockSignals(True)
        self._table_note.setText(self._table_note_value)
        self._table_note.blockSignals(False)
        self._cols.set_columns(columns or [])
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

    # ── inline note editing ────────────────────────────────────────────────────

    def _on_table_note_done(self) -> None:
        text = self._table_note.text().strip()
        if text == self._table_note_value:
            return
        self._table_note_value = text
        self.note_edited.emit("", text)

    def _on_column_note(self, column: str, text: str) -> None:
        if column:
            self.note_edited.emit(column, text)

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
