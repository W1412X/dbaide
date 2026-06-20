"""Fullscreen chart interaction viewer — zoom/pan without hijacking chat scroll."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.markdown_webview import try_create_webengine_view, _configure_webengine_view
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.vendor_assets import (
    echarts_script_src,
    hljs_script_src,
    marked_script_src,
    webengine_html_base,
)
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t
from dbaide.rendering.answer_render import build_answer_document_html, with_chart_interactive


class ChartInteractionDialog(ChromeDialog):
    """Dedicated scroll surface for interactive chart zoom and range sliders."""

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
        self.setWindowTitle(t("ask.interactive_charts"))
        self.setModal(True)
        self.resize(920, 680)
        self.setMinimumSize(720, 520)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(12)

        heading = QLabel(t("ask.interactive_charts"))
        heading.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        root.addWidget(heading)

        hint = QLabel(t("ask.interactive_charts_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px; background: transparent;")
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        host = QWidget()
        host.setStyleSheet(f"background: {Theme.BG}; border-radius: 8px;")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        interactive_theme = with_chart_interactive(theme, interactive=True)
        marked_src = marked_script_src()
        hljs_src = hljs_script_src()
        echarts_src = echarts_script_src()
        html, _blocks = build_answer_document_html(
            answer,
            charts,
            theme=interactive_theme,
            marked_src=marked_src,
            hljs_src=hljs_src,
            echarts_src=echarts_src,
            document_title=title,
            standalone=True,
        )

        view_cls = try_create_webengine_view()
        if view_cls is None:
            fallback = QLabel(t("ask.export_preview_unavailable"))
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setWordWrap(True)
            fallback.setStyleSheet(f"color: {Theme.MUTED}; padding: 32px; background: transparent;")
            host_layout.addWidget(fallback)
        else:
            view = view_cls()
            _configure_webengine_view(view)
            try:
                view.page().setBackgroundColor(QColor(str(theme.get("bg") or Theme.BG)))
            except Exception:
                pass
            base_url = webengine_html_base(marked_src, hljs_src, echarts_src)
            view.setHtml(html, base_url)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            view.setMinimumHeight(560)
            host_layout.addWidget(view)

        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        close_btn = compact_button(t("dialog.close"), width=96)
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        if not any(b.get("type") == "chart" for b in _blocks):
            hint.setText(t("ask.interactive_charts_empty"))


def open_chart_interaction_dialog(
    parent: QWidget | None,
    *,
    answer: str,
    charts: list[dict[str, Any]] | None,
    title: str = "",
    theme: dict[str, Any] | None = None,
) -> None:
    from dbaide.desktop.components.answer_document import answer_theme_payload

    dialog = ChartInteractionDialog(
        parent,
        answer=answer,
        charts=charts,
        title=title,
        theme=theme or answer_theme_payload(),
    )
    dialog.exec()
