"""Saved join catalog management panel (shown in popup)."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.dialogs.message_dialog import alert as dialog_alert, confirm as dialog_confirm, warn as dialog_warn

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_form, form_label
from dbaide.desktop.theme import app_style, Theme


class JoinEditorDialog(QDialog):
    def __init__(self, parent=None, *, initial: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(app_style())
        self.setWindowTitle("Edit Join" if initial else "Add Join")
        layout = QFormLayout(self)
        configure_form(layout)
        initial = initial or {}
        self.table = QLineEdit(str(initial.get("table") or ""))
        self.column = QLineEdit(str(initial.get("column") or ""))
        self.ref_table = QLineEdit(str(initial.get("ref_table") or ""))
        self.ref_column = QLineEdit(str(initial.get("ref_column") or ""))
        self.database = QLineEdit(str(initial.get("database") or ""))
        self.reason = QLineEdit(str(initial.get("reason") or ""))
        for label, widget in (
            ("Left table", self.table),
            ("Left column", self.column),
            ("Right table", self.ref_table),
            ("Right column", self.ref_column),
            ("Database (optional)", self.database),
            ("Note (optional)", self.reason),
        ):
            layout.addRow(form_label(label), widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setIcon(svg_icon("check", color=Theme.GREEN, size=14))
        if cancel_btn is not None:
            cancel_btn.setIcon(svg_icon("x", color=Theme.TEXT_2, size=14))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def payload(self) -> dict[str, str]:
        return {
            "table": self.table.text().strip(),
            "column": self.column.text().strip(),
            "ref_table": self.ref_table.text().strip(),
            "ref_column": self.ref_column.text().strip(),
            "database": self.database.text().strip(),
            "reason": self.reason.text().strip(),
        }


class JoinsTab(QWidget):
    refresh_requested = pyqtSignal()
    add_requested = pyqtSignal(dict)
    update_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        hint = QLabel("User joins (0.99) · Agent-saved candidates · sorted by confidence")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.btn_add = compact_button("Add", icon=svg_icon("plus", color=Theme.TEXT_2, size=14), width=76)
        self.btn_edit = compact_button("Edit", icon=svg_icon("pencil", color=Theme.TEXT_2, size=14), width=76)
        self.btn_delete = compact_button("Delete", icon=svg_icon("trash", color=Theme.TEXT_2, size=14), width=86)
        self.btn_refresh = compact_button("Refresh", icon=svg_icon("refresh", color=Theme.TEXT_2, size=14), width=92)
        for btn in (self.btn_add, self.btn_edit, self.btn_delete, self.btn_refresh):
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete.clicked.connect(self._delete)
        self._records: list[dict[str, Any]] = []

    def load(self, records: list[dict[str, Any]]) -> None:
        self._records = list(records)
        self.list.clear()
        if not records:
            item = QListWidgetItem("No saved joins. Add one or run a multi-table Ask query.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for rec in records:
            try:
                conf = float(rec.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            source = str(rec.get("source") or "?")
            line = (
                f"[{conf:.0%} · {source}] "
                f"{rec.get('table')}.{rec.get('column')} → {rec.get('ref_table')}.{rec.get('ref_column')}"
            )
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, str(rec.get("id") or ""))
            item.setToolTip(str(rec.get("reason") or ""))
            self.list.addItem(item)

    def _selected_id(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _selected_record(self) -> dict[str, Any] | None:
        join_id = self._selected_id()
        if not join_id:
            return None
        for rec in self._records:
            if str(rec.get("id") or "") == join_id:
                return rec
        return None

    def _add(self) -> None:
        dialog = JoinEditorDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not all(payload.get(k) for k in ("table", "column", "ref_table", "ref_column")):
            dialog_warn(self, "Join", "All four endpoint fields are required.")
            return
        payload["source"] = "user"
        self.add_requested.emit(payload)

    def _edit(self) -> None:
        rec = self._selected_record()
        if not rec:
            dialog_alert(self, "Join", "Select a join to edit.")
            return
        dialog = JoinEditorDialog(self, initial=rec)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not all(payload.get(k) for k in ("table", "column", "ref_table", "ref_column")):
            dialog_warn(self, "Join", "All four endpoint fields are required.")
            return
        payload["id"] = rec.get("id")
        self.update_requested.emit(payload)

    def _delete(self) -> None:
        join_id = self._selected_id()
        if not join_id:
            dialog_alert(self, "Join", "Select a join to delete.")
            return
        if not dialog_confirm(self, "Delete join", "Remove this saved join?"):
            return
        self.delete_requested.emit(join_id)
