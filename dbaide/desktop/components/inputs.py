from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
)


def configure_form(form: QFormLayout) -> None:
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    form.setContentsMargins(0, 0, 0, 0)


def configure_wrapped_label(label: QLabel, *, max_width: int | None = None) -> None:
    """Allow QLabel word-wrap inside constrained layouts (avoids horizontal overflow)."""
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    if max_width is not None:
        label.setMaximumWidth(max_width)


def configure_multiline_text_edit(
    edit: QTextEdit | QPlainTextEdit,
    *,
    min_height: int = 88,
    max_height: int = 200,
    padding: int = 24,
) -> None:
    if isinstance(edit, QTextEdit):
        edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    else:
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    edit.setMinimumHeight(min_height)
    edit.setMaximumHeight(max_height)
    edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    sync_multiline_height(edit, min_height=min_height, max_height=max_height, padding=padding)


def sync_multiline_height(
    edit: QTextEdit | QPlainTextEdit,
    *,
    min_height: int,
    max_height: int,
    padding: int = 24,
) -> int:
    viewport_w = max(40, edit.viewport().width())
    if isinstance(edit, QTextEdit):
        doc = edit.document()
        doc.setTextWidth(viewport_w)
        content_h = doc.documentLayout().documentSize().height()
    else:
        doc = edit.document()
        doc.setTextWidth(viewport_w)
        content_h = doc.size().height()
    frame = edit.frameWidth() * 2
    height = int(content_h + padding + frame)
    height = max(min_height, min(height, max_height))
    edit.setFixedHeight(height)
    return height


def configure_readonly_text_view(view: QTextEdit) -> None:
    view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


class DropdownCombo(QComboBox):
    """Compact combo with capped popup height."""

    def __init__(self, parent=None, *, max_visible: int = 8) -> None:
        super().__init__(parent)
        self.setMaxVisibleItems(max_visible)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(34)
        self.setMinimumWidth(120)
        self.setMaximumWidth(240)

    def current_value(self) -> str:
        return str(self.currentData() or "")
