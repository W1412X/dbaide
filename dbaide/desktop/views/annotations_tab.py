"""User annotations (notes on db/table/column) management panel.

Mirrors the Saved Joins manager (``joins_tab.py``) so the look, layout and
signal contract stay consistent. Styling uses ``Theme`` tokens + ``app_style()``
so it follows the active light/dark palette."""

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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
    QVBoxLayout,
)

from dbaide.desktop.components.inputs import configure_form, form_label
from dbaide.desktop.theme import app_style, Theme
from dbaide.i18n import t as _t


class AnnotationEditorDialog(QDialog):
    """Add a note (identity fields editable) or edit an existing note (note only)."""

    def __init__(self, parent=None, *, initial: dict[str, Any] | None = None,
                 prefill: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(app_style())
        self._editing = bool(initial)
        self.setWindowTitle(_t("notes.edit_title") if initial else _t("notes.add_title"))
        seed = initial or prefill or {}
        layout = QFormLayout(self)
        configure_form(layout)

        self.database = QLineEdit(str(seed.get("database") or ""))
        self.table = QLineEdit(str(seed.get("table") or ""))
        self.column = QLineEdit(str(seed.get("column") or ""))
        self.note = QPlainTextEdit(str(seed.get("note") or ""))
        self.note.setMinimumHeight(96)
        self.note.setPlaceholderText(
            "e.g. UTC timestamp, show +8 · this table is deprecated, use orders_v2"
        )

        layout.addRow(form_label(_t("notes.field_database")), self.database)
        layout.addRow(form_label(_t("notes.field_table")), self.table)
        layout.addRow(form_label(_t("notes.field_column")), self.column)
        layout.addRow(form_label(_t("notes.field_note")), self.note)

        # The object identity is the key — editing it would orphan the note, so in
        # edit mode the identity fields are locked and only the text is editable.
        if self._editing:
            for w in (self.database, self.table, self.column):
                w.setReadOnly(True)
                w.setStyleSheet(f"color: {Theme.MUTED};")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self._initial = initial or {}

    def payload(self) -> dict[str, str]:
        data = {
            "database": self.database.text().strip(),
            "table": self.table.text().strip(),
            "column": self.column.text().strip(),
            "note": self.note.toPlainText().strip(),
        }
        if self._editing and self._initial.get("id"):
            data["id"] = str(self._initial["id"])
        return data


class AnnotationsTab(QWidget):
    refresh_requested = pyqtSignal()
    add_requested = pyqtSignal(dict)
    update_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        hint = QLabel(_t("notes.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.btn_add = QPushButton(_t("notes.add"))
        self.btn_edit = QPushButton(_t("notes.edit"))
        self.btn_delete = QPushButton(_t("notes.delete"))
        self.btn_refresh = QPushButton(_t("notes.refresh"))
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
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        self._records: list[dict[str, Any]] = []

    # ── data ────────────────────────────────────────────────────────────────

    def load(self, records: list[dict[str, Any]]) -> None:
        self._records = list(records)
        self.list.clear()
        if not records:
            item = QListWidgetItem(_t("notes.empty"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for rec in records:
            scope = str(rec.get("scope") or "")
            scope_label = _t(f"notes.scope_{scope}") if scope in ("database", "table", "column") else scope
            line = f"[{scope_label}] {self._target_label(rec)}  ·  {rec.get('note') or ''}"
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, str(rec.get("id") or ""))
            item.setToolTip(str(rec.get("note") or ""))
            self.list.addItem(item)

    @staticmethod
    def _target_label(rec: dict[str, Any]) -> str:
        parts = [str(rec.get(k) or "").strip() for k in ("database", "table", "column")]
        parts = [p for p in parts if p]
        return ".".join(parts) if parts else "(connection-wide)"

    def _selected_id(self) -> str:
        item = self.list.currentItem()
        return "" if item is None else str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _selected_record(self) -> dict[str, Any] | None:
        ann_id = self._selected_id()
        if not ann_id:
            return None
        for rec in self._records:
            if str(rec.get("id") or "") == ann_id:
                return rec
        return None

    # ── actions ───────────────────────────────────────────────────────────────

    def _add(self) -> None:
        self._run_add(AnnotationEditorDialog(self))

    def _run_add(self, dialog: AnnotationEditorDialog) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload.get("note"):
            QMessageBox.warning(self, _t("notes.title"), _t("notes.note_required"))
            return
        if payload.get("column") and not payload.get("table"):
            QMessageBox.warning(self, _t("notes.title"), _t("notes.column_needs_table"))
            return
        self.add_requested.emit(payload)

    def _edit(self) -> None:
        rec = self._selected_record()
        if not rec:
            return
        dialog = AnnotationEditorDialog(self, initial=rec)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload.get("note"):
            QMessageBox.warning(self, _t("notes.title"), _t("notes.note_required"))
            return
        self.update_requested.emit(payload)

    def _delete(self) -> None:
        ann_id = self._selected_id()
        if not ann_id:
            return
        if QMessageBox.question(self, _t("notes.title"), _t("notes.delete_confirm")) != QMessageBox.StandardButton.Yes:
            return
        self.delete_requested.emit(ann_id)
