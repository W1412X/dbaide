"""Qt WebEngine wrapper for unified assistant answer documents."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
)

from dbaide.desktop.components.base import discard_widget
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.markdown_webview import (
    estimate_markdown_height,
    try_create_webengine_view,
    _configure_webengine_view,
    _HEIGHT_JS,
    _HEIGHT_SETTLE_MS,
)
from dbaide.desktop.components.md_css import markdown_stylesheet
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import (
    echarts_script_src,
    hljs_script_src,
    marked_script_src,
    webengine_html_base,
)
from dbaide.rendering.answer_render import (
    build_answer_document_html,
    theme_payload_from_palette,
)
from dbaide.rendering.compose import compose_blocks
from dbaide.rendering.markdown import render_markdown_safe


def chart_theme_payload(*, background: str | None = None) -> dict[str, Any]:
    return answer_theme_payload(background=background)


def answer_theme_payload(*, background: str | None = None) -> dict[str, Any]:
    palette = {
        "BG": Theme.BG,
        "TEXT": Theme.TEXT,
        "TEXT_2": Theme.TEXT_2,
        "MUTED": Theme.MUTED,
        "BORDER_SOFT": Theme.BORDER_SOFT,
        "CODE_BG": Theme.CODE_BG,
        "PANEL": Theme.PANEL,
        "PANEL_2": Theme.PANEL_2,
        "BLUE": Theme.BLUE,
        "ACCENT": Theme.ACCENT,
        "GREEN": Theme.GREEN,
        "YELLOW": Theme.YELLOW,
        "RED": Theme.RED,
    }
    return theme_payload_from_palette(palette, background=background)


def estimate_answer_height(
    answer: str,
    charts: list[dict[str, Any]] | None,
    *,
    width: int = 640,
) -> int:
    """Rough height before WebEngine finishes layout."""
    theme = answer_theme_payload()
    blocks = compose_blocks(answer, charts, theme=theme)
    md_parts = [str(b.get("source") or "") for b in blocks if b.get("type") == "markdown"]
    height = estimate_markdown_height("\n\n".join(md_parts), width=width)
    for block in blocks:
        if block.get("type") == "chart":
            height += int(block.get("height") or 320) + 36
    return max(24, height)


def _fallback_plaintext(answer: str, charts: list[dict[str, Any]] | None) -> str:
    theme = answer_theme_payload()
    blocks = compose_blocks(answer, charts, theme=theme)
    parts: list[str] = []
    for block in blocks:
        if block.get("type") == "markdown":
            parts.append(str(block.get("source") or ""))
        elif block.get("type") == "chart":
            title = str(block.get("title") or block.get("chart_id") or "Chart").strip()
            parts.append(f"[{title}]")
    return "\n\n".join(p for p in parts if p.strip())


class AnswerDocumentWidget(QFrame):
    """Fixed-height WebEngine host for a composed answer document."""

    ready = pyqtSignal()

    def __init__(
        self,
        answer: str,
        charts: list[dict[str, Any]] | None = None,
        *,
        background: str | None = None,
        fast_render: bool = False,
        defer_show: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._answer = str(answer or "")
        self._charts = [dict(c) for c in (charts or []) if isinstance(c, dict)]
        self._background = str(background or Theme.BG)
        self._defer_show = bool(defer_show)
        self._ready_emitted = False
        self._page_loaded = False
        self._last_height = 0
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(32)
        self._height_timer.timeout.connect(self._sync_height)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._finalize_ready)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        theme = answer_theme_payload(background=self._background)
        marked_src = marked_script_src()
        hljs_src = hljs_script_src()
        echarts_src = echarts_script_src()
        html, self._blocks = build_answer_document_html(
            self._answer,
            self._charts,
            theme=theme,
            marked_src=marked_src,
            hljs_src=hljs_src,
            echarts_src=echarts_src,
        )

        view_cls = None if fast_render else try_create_webengine_view()
        if view_cls is None:
            self._view = self._build_fallback_browser()
            self._page_loaded = True
            self._defer_show = False
        else:
            est = estimate_answer_height(self._answer, self._charts, width=640)
            self._view = view_cls(self)
            _configure_webengine_view(self._view)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
            self._apply_height(est)
            base_url = webengine_html_base(marked_src, hljs_src, echarts_src)
            self._view.setHtml(html, base_url)
            try:
                self._view.page().setBackgroundColor(QColor(self._background))
            except Exception:
                pass
            load_finished = getattr(self._view, "loadFinished", None)
            if load_finished is not None:
                load_finished.connect(self._on_load_finished)
            else:
                QTimer.singleShot(0, lambda: self._on_load_finished(True))
            if self._defer_show:
                self.hide()
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._view)
        self._schedule_height_sync()
        if self._page_loaded and not isinstance(self._view, QTextBrowser):
            return
        self._finalize_ready()

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return list(self._blocks)

    def _build_fallback_browser(self) -> QTextBrowser:
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setFont(QFont("Inter", 13))
        configure_readonly_text_view(browser)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setStyleSheet(
            f"QTextBrowser {{ background: {self._background}; border: none; color: {Theme.TEXT}; padding: 0; }}"
        )
        browser.document().setDefaultStyleSheet(markdown_stylesheet())
        browser.setHtml(render_markdown_safe(_fallback_plaintext(self._answer, self._charts)))
        browser.document().documentLayout().documentSizeChanged.connect(self._schedule_height_sync)
        return browser

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_height_sync()

    def _on_load_finished(self, _ok: bool = True) -> None:
        self._page_loaded = True
        self._schedule_height_sync()
        self._settle_timer.start(_HEIGHT_SETTLE_MS)

    def _schedule_height_sync(self, *_args) -> None:
        self._height_timer.start()

    def _apply_height(self, height: int) -> None:
        height = max(24, int(height) + 2)
        if abs(height - self._last_height) < 2:
            return
        self._last_height = height
        self._view.setFixedHeight(height)
        self.setFixedHeight(height)

    def _sync_height(self, *_args) -> None:
        if isinstance(self._view, QTextBrowser):
            doc = self._view.document()
            width = max(self._view.viewport().width(), self.width() - 32, 320)
            doc.setTextWidth(width)
            height = int(doc.documentLayout().documentSize().height()) + 4
            self._apply_height(height)
            return
        page = getattr(self._view, "page", lambda: None)()
        if page is None:
            if self._page_loaded:
                self._finalize_ready()
            return

        def apply_height(raw: object) -> None:
            try:
                self._apply_height(int(float(raw)))
            except (TypeError, ValueError):
                return

        page.runJavaScript(_HEIGHT_JS, apply_height)

    def _finalize_ready(self) -> None:
        if self._ready_emitted:
            return
        if not self._page_loaded:
            return
        self._sync_height()
        self._ready_emitted = True
        if self._defer_show:
            self.show()
        self.ready.emit()


def build_answer_document_widget(
    answer: str,
    charts: list[dict[str, Any]] | None = None,
    *,
    background: str | None = None,
    fast_render: bool = False,
    defer_show: bool = False,
    parent=None,
) -> AnswerDocumentWidget:
    return AnswerDocumentWidget(
        answer,
        charts,
        background=background,
        fast_render=fast_render,
        defer_show=defer_show,
        parent=parent,
    )


class AnswerDocumentBlock(QFrame):
    """Assistant answer: plain-text stream while generating, composed WebEngine when done."""

    def __init__(
        self,
        answer: str = "",
        charts: list[dict[str, Any]] | None = None,
        *,
        title: str = "",
        title_tooltip: str = "",
        fast_render: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._answer = str(answer or "")
        self._charts = [dict(c) for c in (charts or []) if isinstance(c, dict)]
        self._background = Theme.BG
        self._fast_render = bool(fast_render)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("answerBlock")
        self.setStyleSheet("QFrame#answerBlock { background: transparent; border: none; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if title:
            label = QLabel(title)
            label.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            label.setStyleSheet(
                f"color: {Theme.MUTED}; background: transparent; letter-spacing: 0.3px;"
            )
            if title_tooltip:
                label.setToolTip(title_tooltip)
            layout.addWidget(label)
        self._content_layout = layout
        self._stream_view: QPlainTextEdit | None = None
        self._stream_shown_len = 0
        self._rendered: AnswerDocumentWidget | None = None
        self._pending_rendered: AnswerDocumentWidget | None = None
        self._stream_height_timer = QTimer(self)
        self._stream_height_timer.setSingleShot(True)
        self._stream_height_timer.setInterval(48)
        self._stream_height_timer.timeout.connect(self._sync_stream_height)
        self._last_stream_height = 0
        if self._answer.strip() or self._charts:
            self._start_render(defer_show=False)

    @property
    def markdown(self) -> str:
        return self._answer

    def set_streaming_text(self, text: str) -> None:
        text = str(text or "")
        self._answer = text
        self._ensure_stream_view()
        view = self._stream_view
        if view is None:
            return
        shown = self._stream_shown_len
        if shown > len(text):
            view.setPlainText(text)
            shown = 0
        if len(text) > shown:
            view.setUpdatesEnabled(False)
            try:
                if shown == 0:
                    view.setPlainText(text)
                else:
                    cursor = view.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    cursor.insertText(text[shown:])
                self._stream_shown_len = len(text)
            finally:
                view.setUpdatesEnabled(True)
        self._schedule_stream_height()

    def set_answer(
        self,
        answer: str,
        charts: list[dict[str, Any]] | None = None,
        *,
        force_rebuild: bool = False,
    ) -> None:
        self._answer = str(answer or "")
        self._charts = [dict(c) for c in (charts or []) if isinstance(c, dict)]
        if not self._answer.strip() and not self._charts:
            self._teardown_stream_view()
            self._teardown_rendered()
            return
        replacing_stream = self._stream_view is not None
        if force_rebuild:
            self._teardown_rendered()
        self._start_render(defer_show=replacing_stream)
        if not replacing_stream:
            self._teardown_stream_view()

    def ensure_full_render(self) -> None:
        """Upgrade bulk-load fast path to WebEngine when charts need real rendering."""
        if not self._charts or not self._fast_render:
            return
        self._fast_render = False
        self.set_answer(self._answer, self._charts, force_rebuild=True)

    def _schedule_stream_height(self) -> None:
        self._stream_height_timer.start()

    def _ensure_stream_view(self) -> None:
        if self._stream_view is not None:
            return
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setFont(QFont("Inter", 13))
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {Theme.TEXT}; padding: 0; }}"
        )
        view.document().contentsChanged.connect(self._sync_stream_height)
        self._content_layout.addWidget(view)
        self._stream_view = view
        self._stream_shown_len = 0
        self._last_stream_height = 0

    def _teardown_stream_view(self) -> None:
        if self._stream_view is None:
            self._stream_shown_len = 0
            self._last_stream_height = 0
            return
        self._stream_view.document().contentsChanged.disconnect(self._sync_stream_height)
        self._content_layout.removeWidget(self._stream_view)
        discard_widget(self._stream_view)
        self._stream_view = None
        self._stream_shown_len = 0
        self._last_stream_height = 0

    def _sync_stream_height(self, *_args) -> None:
        view = self._stream_view
        if view is None:
            return
        doc = view.document()
        width = max(view.viewport().width(), self.width() - 32, 320)
        doc.setTextWidth(width)
        height = int(doc.size().height()) + 8
        height = max(height, 24)
        if abs(height - self._last_stream_height) < 2:
            return
        self._last_stream_height = height
        view.setFixedHeight(height)

    def _teardown_rendered(self) -> None:
        if self._pending_rendered is not None:
            try:
                self._pending_rendered.ready.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._content_layout.removeWidget(self._pending_rendered)
            discard_widget(self._pending_rendered)
            self._pending_rendered = None
        if self._rendered is None:
            return
        self._content_layout.removeWidget(self._rendered)
        discard_widget(self._rendered)
        self._rendered = None

    def _start_render(self, *, defer_show: bool = False) -> None:
        if self._pending_rendered is not None:
            self._teardown_rendered()
        if not self._answer.strip() and not self._charts:
            return
        use_defer = defer_show and not self._fast_render
        widget = build_answer_document_widget(
            self._answer,
            self._charts,
            background=self._background,
            fast_render=self._fast_render,
            defer_show=use_defer,
        )
        widget.ready.connect(lambda w=widget: self._commit_rendered(w))
        self._content_layout.addWidget(widget)
        self._pending_rendered = widget
        if widget._ready_emitted:
            self._commit_rendered(widget)

    def _commit_rendered(self, widget: AnswerDocumentWidget) -> None:
        if self._pending_rendered is not widget:
            return
        self._teardown_stream_view()
        if self._rendered is not None and self._rendered is not widget:
            old = self._rendered
            self._rendered = None
            self._content_layout.removeWidget(old)
            discard_widget(old)
        widget.show()
        self._rendered = widget
        self._pending_rendered = None

    def copy_message(self) -> None:
        if self._stream_view is not None:
            text = self._answer or self._stream_view.toPlainText()
        else:
            text = self._answer
        if text:
            QApplication.clipboard().setText(text)
