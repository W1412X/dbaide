"""Small themed single-line text input dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t


class TextInputDialog(ChromeDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        label: str,
        *,
        text: str = "",
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 15px; font-weight: 700; background: transparent;"
        )
        heading.setWordWrap(True)
        root.addWidget(heading)

        prompt = QLabel(label)
        prompt.setStyleSheet(
            f"color: {Theme.TEXT_2}; font-size: 12px; font-weight: 500; background: transparent;"
        )
        prompt.setWordWrap(True)
        root.addWidget(prompt)

        self._input = QLineEdit(str(text or ""))
        self._input.setPlaceholderText(str(placeholder or ""))
        self._input.selectAll()
        self._input.returnPressed.connect(self._submit)
        root.addWidget(self._input)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 2, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)

        cancel_btn = compact_button(t("dialog.cancel"), width=88)
        ok_btn = compact_button(t("dialog.confirm"), primary=True, width=96)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._submit)
        actions.addWidget(cancel_btn)
        actions.addWidget(ok_btn)
        root.addLayout(actions)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _submit(self) -> None:
        if self.value():
            self.accept()

    def value(self) -> str:
        return self._input.text().strip()


def get_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    text: str = "",
    placeholder: str = "",
) -> tuple[str, bool]:
    dialog = TextInputDialog(parent, title, label, text=text, placeholder=placeholder)
    ok = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.value(), ok
