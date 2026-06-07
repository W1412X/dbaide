"""Themed alert / confirm dialogs — drop-in replacements for native QMessageBox."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme, app_style


class MessageDialog(QDialog):
    """Simple modal with title, body, and one or two action buttons."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        confirm: bool = False,
        ok_label: str = "",
        cancel_label: str = "",
    ) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(14)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        heading.setWordWrap(True)
        root.addWidget(heading)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color: {Theme.TEXT_2}; font-size: 13px; line-height: 1.45; background: transparent;"
        )
        root.addWidget(body)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)

        cancel_text = cancel_label or t("dialog.cancel")
        ok_text = ok_label or (t("dialog.confirm") if confirm else t("dialog.ok"))

        if confirm:
            cancel_btn = compact_button(cancel_text, width=88)
            cancel_btn.clicked.connect(self.reject)
            actions.addWidget(cancel_btn)

        ok_btn = compact_button(ok_text, primary=True, width=96)
        ok_btn.clicked.connect(self.accept)
        actions.addWidget(ok_btn)
        root.addLayout(actions)


def alert(parent: QWidget | None, title: str, message: str) -> None:
    dialog = MessageDialog(parent, title, message)
    dialog.exec()


def warn(parent: QWidget | None, title: str, message: str) -> None:
    alert(parent, title, message)


def confirm(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    ok_label: str = "",
    cancel_label: str = "",
) -> bool:
    dialog = MessageDialog(
        parent,
        title,
        message,
        confirm=True,
        ok_label=ok_label,
        cancel_label=cancel_label,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted
