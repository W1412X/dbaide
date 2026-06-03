"""Table structure view — columns grid + generated DDL (DBeaver-style).

Renders from the schema asset already in memory (the columns carried by the tree
node), so opening it is instant — no extra database round-trip. Columns show
name/type/key; a generated CREATE TABLE skeleton is shown below.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget

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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
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
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        self._title = QLabel("")
        self._title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        pl.addWidget(self._title)
        self._cols = ResultTableWidget()
        self._cols.meta.setVisible(False)
        pl.addWidget(self._cols, 1)
        ddl_label = QLabel(t("structure.ddl"))
        ddl_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600;")
        pl.addWidget(ddl_label)
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

    def show_table(self, table: str, columns: list[dict[str, Any]]) -> None:
        self._title.setText(table)
        rows = [{
            "Column": c.get("name", ""),
            "Type": c.get("data_type") or "",
            "Key": "PK" if c.get("primary_key") else ("indexed" if c.get("indexed") else " "),
        } for c in (columns or [])]
        self._cols.load(columns=["Column", "Type", "Key"], rows=rows, row_count=len(rows))
        self._ddl.setPlainText(_generate_ddl(table, columns or []))
        self.stack.setCurrentIndex(1)
