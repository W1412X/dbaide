"""Dialog for exporting assistant answers as HTML with padding preview."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button, ghost_action_button
from dbaide.desktop.components.inputs import compact_field_column, dialog_action_column, STANDARD_FIELD_HEIGHT
from dbaide.desktop.components.markdown_webview import try_create_webengine_view
from dbaide.desktop.dialogs.file_dialogs import get_save_file_name
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t
from dbaide.rendering.answer_export import export_answer_html, suggest_export_filename
from dbaide.rendering.answer_render import format_root_padding

_SIDEBAR_WIDTH = 252


class AnswerExportDialog(ChromeDialog):
    """Configure export padding, preview live, then copy or save HTML."""

    _PREVIEW_DEBOUNCE_MS = 120

    def __init__(
        self,
        parent: QWidget | None,
        *,
        answer: str,
        charts: list[dict[str, Any]] | None,
        title: str,
        theme: dict[str, Any],
    ) -> None:
        super().__init__(parent)
        self._answer = str(answer or "")
        self._charts = [dict(c) for c in (charts or []) if isinstance(c, dict)]
        self._title = str(title or "")
        self._theme = dict(theme)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(self._PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self.setWindowTitle(t("ask.export_answer_html"))
        self.setModal(True)
        self.resize(920, 620)
        self.setMinimumSize(780, 500)
        self.setStyleSheet(app_style())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("exportSidebar")
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        sidebar.setStyleSheet(
            f"""
            QFrame#exportSidebar {{
                background: {Theme.SURFACE};
                border: none;
                border-right: 1px solid {Theme.BORDER_SOFT};
            }}
            """
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 20, 18, 18)
        sidebar_layout.setSpacing(14)

        heading = QLabel(t("ask.export_answer_html"))
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 15px; font-weight: 700; background: transparent;"
        )
        sidebar_layout.addWidget(heading)

        hint = QLabel(t("ask.export_answer_html_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 12px; line-height: 1.45; background: transparent;"
        )
        sidebar_layout.addWidget(hint)

        pad_label = QLabel(t("ask.export_padding"))
        pad_label.setStyleSheet(
            f"color: {Theme.TEXT_2}; font-size: 11px; font-weight: 600;"
            f" letter-spacing: 0.3px; background: transparent;"
        )
        sidebar_layout.addWidget(pad_label)

        self._spins = {
            "top": self._make_spin(),
            "right": self._make_spin(),
            "bottom": self._make_spin(),
            "left": self._make_spin(),
        }
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for row, col, key, label_key in (
            (0, 0, "top", "ask.export_padding_top"),
            (0, 1, "right", "ask.export_padding_right"),
            (1, 0, "bottom", "ask.export_padding_bottom"),
            (1, 1, "left", "ask.export_padding_left"),
        ):
            grid.addWidget(
                compact_field_column(t(label_key), self._spins[key], height=STANDARD_FIELD_HEIGHT),
                row,
                col,
            )
        sidebar_layout.addLayout(grid)

        preset_host = QWidget()
        preset_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        preset_col = QVBoxLayout(preset_host)
        preset_col.setContentsMargins(0, 0, 0, 0)
        preset_col.setSpacing(6)
        preset_col.addWidget(self._preset_btn(
            t("ask.export_padding_preset_embedded"),
            lambda: self._apply_padding_preset(0, 2, 0, 0),
        ))
        preset_col.addWidget(self._preset_btn(
            t("ask.export_padding_preset_comfortable"),
            lambda: self._apply_padding_preset(16, 20, 32, 20),
        ))
        sidebar_layout.addWidget(preset_host)
        sidebar_layout.addStretch(1)

        actions_host, actions_col = dialog_action_column()
        self._save_btn = compact_button(t("ask.export_save_html"), primary=True)
        self._save_btn.clicked.connect(self._save_html)
        self._copy_btn = compact_button(t("ask.export_copy_html"))
        self._copy_btn.clicked.connect(self._copy_html)
        cancel_btn = ghost_action_button(t("dialog.cancel"))
        cancel_btn.clicked.connect(self.reject)
        actions_col.addWidget(self._save_btn)
        actions_col.addWidget(self._copy_btn)
        actions_col.addWidget(cancel_btn)
        sidebar_layout.addWidget(actions_host)
        root.addWidget(sidebar)

        preview_host = QFrame()
        preview_host.setObjectName("exportPreviewHost")
        preview_host.setStyleSheet(
            f"QFrame#exportPreviewHost {{ background: {Theme.BG}; border: none; }}"
        )
        preview_layout = QVBoxLayout(preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        view_cls = try_create_webengine_view()
        if view_cls is None:
            fallback = QLabel(t("ask.export_preview_unavailable"))
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setWordWrap(True)
            fallback.setStyleSheet(f"color: {Theme.MUTED}; padding: 32px; background: transparent;")
            self._preview = fallback
        else:
            self._preview = view_cls()
            try:
                self._preview.page().setBackgroundColor(QColor(str(self._theme.get("bg") or Theme.BG)))
            except Exception:
                pass
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self._preview, 1)
        root.addWidget(preview_host, 1)

        self._apply_padding_preset(16, 20, 32, 20)

    def _preset_btn(self, text: str, handler) -> QWidget:
        btn = ghost_action_button(text)
        btn.clicked.connect(handler)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return btn

    def _make_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 160)
        spin.valueChanged.connect(self._schedule_preview)
        return spin

    def _apply_padding_preset(self, top: int, right: int, bottom: int, left: int) -> None:
        mapping = {"top": top, "right": right, "bottom": bottom, "left": left}
        for key, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(mapping[key])
            spin.blockSignals(False)
        self._schedule_preview()

    def _current_padding(self) -> str:
        return format_root_padding(
            self._spins["top"].value(),
            self._spins["right"].value(),
            self._spins["bottom"].value(),
            self._spins["left"].value(),
        )

    def _build_html(self) -> str:
        return export_answer_html(
            self._answer,
            self._charts,
            title=self._title,
            theme=self._theme,
            root_padding=self._current_padding(),
        )

    def _schedule_preview(self, *_args) -> None:
        if not self._preview_timer.isActive():
            self._refresh_preview()
        self._preview_timer.start()

    def _refresh_preview(self) -> None:
        if not hasattr(self._preview, "setHtml"):
            return
        html = self._build_html()
        self._preview.setHtml(html)

    def _copy_html(self) -> None:
        from PyQt6 import sip
        QApplication.clipboard().setText(self._build_html())
        copied = t("ask.export_copied")
        self._copy_btn.setText(copied)
        # The 1.6s reset can fire after the dialog is closed/destroyed — touching the
        # deleted button would raise RuntimeError. Guard with sip.isdeleted.
        def _reset() -> None:
            if not sip.isdeleted(self._copy_btn):
                self._copy_btn.setText(t("ask.export_copy_html"))
        QTimer.singleShot(1600, _reset)

    def _save_html(self) -> None:
        default_name = suggest_export_filename(self._title)
        path, _ = get_save_file_name(
            self,
            t("ask.export_save_html"),
            default_name,
            "HTML (*.html)",
        )
        if not path:
            return
        if not str(path).lower().endswith(".html"):
            path = f"{path}.html"
        Path(path).write_text(self._build_html(), encoding="utf-8")
        self.accept()


def open_answer_export_dialog(
    parent: QWidget | None,
    *,
    answer: str,
    charts: list[dict[str, Any]] | None,
    title: str = "",
    theme: dict[str, Any] | None = None,
) -> None:
    """Open the export dialog for an assistant answer."""
    from dbaide.desktop.components.answer_document import answer_theme_payload

    dialog = AnswerExportDialog(
        parent,
        answer=answer,
        charts=charts,
        title=title,
        theme=theme or answer_theme_payload(),
    )
    dialog.exec()
