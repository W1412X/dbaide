"""Lightweight editor for a single object's user note (db/table/column).

Opened from the schema tree's pencil icon. The object identity is fixed (passed
in); the user only edits the note text. Clearing the text removes the note."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from dbaide.desktop.theme import app_style, Theme
from dbaide.i18n import t as _t


class NoteEditorDialog(QDialog):
    def __init__(self, parent=None, *, target_label: str = "", note: str = "") -> None:
        super().__init__(parent)
        self.setStyleSheet(app_style())
        self.setWindowTitle(_t("notes.edit_title"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(target_label)
        heading.setStyleSheet(f"color: {Theme.TEXT}; font-size: 13px; font-weight: 600;")
        layout.addWidget(heading)

        sub = QLabel(_t("notes.editor_hint"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        layout.addWidget(sub)

        self.note = QPlainTextEdit(note)
        self.note.setMinimumHeight(120)
        self.note.setPlaceholderText(_t("notes.editor_ph"))
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.note.toPlainText().strip()
