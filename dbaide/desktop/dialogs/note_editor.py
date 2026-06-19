"""Lightweight editor for a single object's user note (db/table/column).

Opened from the schema tree's pencil icon. The object identity is fixed (passed
in); the user only edits the note text. Clearing the text removes the note."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QHBoxLayout, QPlainTextEdit, QVBoxLayout

from dbaide.desktop.components.base import button_icon_color, compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_wrapped_label
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
        configure_wrapped_label(heading)
        heading.setStyleSheet(f"color: {Theme.TEXT}; font-size: 13px; font-weight: 600;")
        layout.addWidget(heading)

        sub = QLabel(_t("notes.editor_hint"))
        configure_wrapped_label(sub)
        sub.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
        layout.addWidget(sub)

        self.note = QPlainTextEdit(note)
        self.note.setMinimumHeight(120)
        self.note.setPlaceholderText(_t("notes.editor_ph"))
        layout.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        cancel_btn = compact_button(_t("btn.cancel"), icon=svg_icon("x", color=Theme.TEXT_2, size=14), width=88)
        ok_btn = compact_button(
            _t("btn.save"),
            primary=True,
            icon=svg_icon("check", color=button_icon_color(primary=True), size=14),
            width=88,
        )
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

    def value(self) -> str:
        return self.note.toPlainText().strip()
