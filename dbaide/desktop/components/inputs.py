"""Form field labels and shared input helpers."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.theme import Theme

# Matches global QSS ``_INPUT`` min/max-height in ``app_style()``.
STANDARD_FIELD_HEIGHT = 28
# Slightly taller controls for narrow modal dialogs (backup, etc.).
COMPACT_DIALOG_FIELD_HEIGHT = 32

# Scoped on form containers — QFormLayout is not a widget, so parent #id rules are required.
FORM_INNER_LABEL_RULES = f"""
    QLabel#formLabel {{
        background-color: rgba(0, 0, 0, 0);
        background: transparent;
        border: none;
        border-width: 0;
        border-radius: 0;
        outline: none;
        color: {Theme.TEXT_2};
        font-size: 13px;
        font-weight: 400;
        padding: 0 10px 0 0;
        margin: 0;
    }}
"""


class FormLabel(QLabel):
    """Right-aligned caption only — never a boxed field."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("formLabel")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(28)
        self.setMinimumWidth(96)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"""
            QLabel#formLabel {{
                background-color: rgba(0, 0, 0, 0);
                background: transparent;
                border: none;
                border-width: 0;
                border-radius: 0;
                color: {Theme.TEXT_2};
                font-size: 13px;
                padding: 0 10px 0 0;
                margin: 0;
            }}
            """
        )


def form_label(text: str) -> FormLabel:
    return FormLabel(text)


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
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    if max_width is not None:
        label.setMaximumWidth(max_width)


def configure_sql_editor_pane(
    edit: QPlainTextEdit,
    *,
    min_height: int = 100,
) -> None:
    """SQL editor inside a splitter — user resizes vertically; no fixed max height."""
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    edit.setMinimumHeight(min_height)
    edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


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


def configure_compact_field(
    widget: QWidget,
    *,
    height: int = STANDARD_FIELD_HEIGHT,
    min_width: int | None = None,
    max_width: int | None = None,
) -> None:
    """Lock vertical size so stretched dialog layouts cannot squash inputs."""
    widget.setFixedHeight(height)
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    if min_width is not None:
        widget.setMinimumWidth(min_width)
    if max_width is not None:
        widget.setMaximumWidth(max_width)


def compact_field_label(text: str, *, muted: bool = True) -> QLabel:
    label = QLabel(text)
    color = Theme.MUTED if muted else Theme.TEXT
    label.setStyleSheet(f"font-size: 11px; color: {color}; background: transparent;")
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label


def compact_field_column(
    label: QLabel | str,
    control: QWidget,
    *,
    height: int = COMPACT_DIALOG_FIELD_HEIGHT,
) -> QWidget:
    """Label + control stack with a fixed vertical footprint."""
    if isinstance(label, str):
        label = compact_field_label(label)
    col = QWidget()
    col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    col_layout = QVBoxLayout(col)
    col_layout.setContentsMargins(0, 0, 0, 0)
    col_layout.setSpacing(4)
    col_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    col_layout.addWidget(label)
    configure_compact_field(control, height=height)
    col_layout.addWidget(control)
    return col


def finalize_compact_dialog(dialog: QWidget) -> None:
    """Ensure a narrow dialog is tall enough for its content."""
    from dbaide.desktop.window_chrome import sync_dialog_minimum_size

    sync_dialog_minimum_size(dialog)


def dialog_action_row(*, top_margin: int = 0, spacing: int = 8) -> tuple[QWidget, QHBoxLayout]:
    """Bottom button row host — keeps actions on their own layout band."""
    host = QWidget()
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, top_margin, 0, 0)
    row.setSpacing(spacing)
    return host, row


def dialog_action_column(*, spacing: int = 8) -> tuple[QWidget, QVBoxLayout]:
    """Vertical action stack for sidebars."""
    host = QWidget()
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    col = QVBoxLayout(host)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(spacing)
    return host, col


class Combo(QComboBox):
    """QComboBox with an opaque frameless dropdown (same approach as ``QMenu`` popups)."""

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        self._style_popup()

    def _style_popup(self) -> None:
        from dbaide.desktop.theme import combo_popup_stylesheet

        view = self.view()
        container = view.window() if view is not None else None
        if container is None:
            return
        css = combo_popup_stylesheet()
        container.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        container.setAutoFillBackground(True)
        container.setStyleSheet(f"background-color: {Theme.PANEL};")
        view.setStyleSheet(css)
        container.update()
        container.show()
