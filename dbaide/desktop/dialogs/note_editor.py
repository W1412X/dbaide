"""Lightweight editor for a single object's user note (db/table/column).

Opened from the schema tree's pencil icon. The object identity is fixed (passed
in); the user only edits the note text. Clearing the text removes the note."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import app_style, Theme
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t as _t


class NoteEditorDialog(ChromeDialog):
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
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setIcon(svg_icon("check", color=Theme.GREEN, size=14))
        if cancel_btn is not None:
            cancel_btn.setIcon(svg_icon("x", color=Theme.TEXT_2, size=14))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.note.toPlainText().strip()
