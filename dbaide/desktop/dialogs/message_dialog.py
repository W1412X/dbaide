"""Themed alert / confirm dialogs — drop-in replacements for native QMessageBox."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import dialog_action_row
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog

_CONTENT_WIDTH = 440
_MIN_BODY_HEIGHT = 44
_MAX_BODY_HEIGHT = 360
_MAX_HELP_BODY_HEIGHT = 520


class MessageDialog(ChromeDialog):
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
        max_body_height: int = _MAX_BODY_HEIGHT,
    ) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(_CONTENT_WIDTH + 48)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(14)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        heading.setWordWrap(True)
        heading.setMinimumWidth(_CONTENT_WIDTH)
        root.addWidget(heading)

        body = QTextBrowser()
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setReadOnly(True)
        body.setOpenExternalLinks(False)
        body.setPlainText(message)
        body.document().setDocumentMargin(0)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setMinimumWidth(_CONTENT_WIDTH)
        body.setStyleSheet(
            f"""
            QTextBrowser {{
                color: {Theme.TEXT_2};
                font-size: 13px;
                background: transparent;
                border: none;
                padding: 0;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
            }}
            """
        )
        self._body = body
        self._max_body_height = max_body_height
        self._sync_body_height()
        root.addWidget(body)

        actions_host, actions = dialog_action_row()
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
        root.addWidget(actions_host)

    def _sync_body_height(self) -> None:
        self._body.document().setTextWidth(_CONTENT_WIDTH)
        doc_height = int(self._body.document().documentLayout().documentSize().height())
        body_height = max(_MIN_BODY_HEIGHT, min(doc_height + 12, self._max_body_height))
        self._body.setMinimumHeight(min(body_height, self._max_body_height))
        self._body.setMaximumHeight(self._max_body_height)
        self._body.resize(_CONTENT_WIDTH, body_height)


def alert(parent: QWidget | None, title: str, message: str, *, max_body_height: int = _MAX_BODY_HEIGHT) -> None:
    dialog = MessageDialog(parent, title, message, max_body_height=max_body_height)
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


class ChoiceDialog(ChromeDialog):
    """Modal with a message and several explicit actions."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        choices: list[tuple[str, str]],
        cancel_label: str = "",
        max_body_height: int = _MAX_BODY_HEIGHT,
    ) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self._choice = ""
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(_CONTENT_WIDTH + 48)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(14)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        heading.setWordWrap(True)
        heading.setMinimumWidth(_CONTENT_WIDTH)
        root.addWidget(heading)

        body = QTextBrowser()
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setReadOnly(True)
        body.setOpenExternalLinks(False)
        body.setPlainText(message)
        body.document().setDocumentMargin(0)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setMinimumWidth(_CONTENT_WIDTH)
        body.setStyleSheet(
            f"""
            QTextBrowser {{
                color: {Theme.TEXT_2};
                font-size: 13px;
                background: transparent;
                border: none;
                padding: 0;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
            }}
            """
        )
        body.document().setTextWidth(_CONTENT_WIDTH)
        doc_height = int(body.document().documentLayout().documentSize().height())
        body_height = max(_MIN_BODY_HEIGHT, min(doc_height + 12, max_body_height))
        body.setMinimumHeight(min(body_height, max_body_height))
        body.setMaximumHeight(max_body_height)
        body.resize(_CONTENT_WIDTH, body_height)
        root.addWidget(body)

        actions_host, actions = dialog_action_row()
        actions.addStretch(1)

        cancel_btn = compact_button(cancel_label or t("dialog.cancel"), width=88)
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)

        for index, (key, label) in enumerate(choices):
            btn = compact_button(label, primary=index == 0)
            btn.clicked.connect(lambda _checked=False, value=key: self._accept_choice(value))
            actions.addWidget(btn)

        root.addWidget(actions_host)

    def _accept_choice(self, choice: str) -> None:
        self._choice = str(choice or "")
        self.accept()

    def choice(self) -> str:
        return self._choice


def choose(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    choices: list[tuple[str, str]],
    cancel_label: str = "",
) -> str | None:
    dialog = ChoiceDialog(parent, title, message, choices=choices, cancel_label=cancel_label)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.choice()
