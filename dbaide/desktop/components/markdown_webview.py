"""Qt WebEngine wrapper for rendered Markdown answers."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextDocument
from PyQt6.QtWidgets import QFrame, QSizePolicy, QTextBrowser, QVBoxLayout

from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.md_css import markdown_stylesheet
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import hljs_script_src, marked_script_src, webengine_html_base
from dbaide.rendering.markdown import render_markdown_safe
from dbaide.rendering.markdown_page import render_markdown_html

# Measure #root only — body/document scrollHeight includes the WebEngine viewport
# and leaves a large empty band above the turn footer.
_HEIGHT_JS = (
    "(function(){"
    "if (typeof measureContentHeight === 'function') return measureContentHeight();"
    "var el=document.getElementById('root');"
    "return el?Math.ceil(el.getBoundingClientRect().height):0;"
    "})()"
)
_HEIGHT_SETTLE_MS = 280


def markdown_theme_payload(*, background: str | None = None) -> dict[str, Any]:
    bg = str(background or Theme.BG)
    return {
        "text": Theme.TEXT,
        "text2": Theme.TEXT_2,
        "muted": Theme.MUTED,
        "border": Theme.BORDER_SOFT,
        "codeBg": Theme.CODE_BG,
        "panel2": Theme.PANEL_2,
        "link": Theme.BLUE,
        "bg": bg,
    }


def estimate_markdown_height(markdown: str, *, width: int = 640) -> int:
    """Rough QTextDocument height for pre-sizing before WebEngine finishes."""
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Inter", 13))
    doc.setHtml(render_markdown_safe(str(markdown or "")))
    doc.setTextWidth(max(200, int(width)))
    return max(24, int(doc.size().height()) + 8)


def try_create_webengine_view():
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        return QWebEngineView
    except Exception:
        return None


def _configure_webengine_view(view) -> None:
    if hasattr(view, "setVerticalScrollBarPolicy"):
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if hasattr(view, "setHorizontalScrollBarPolicy"):
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    try:
        from PyQt6.QtWebEngineCore import QWebEngineSettings

        settings = view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
    except Exception:
        pass


class MarkdownWebWidget(QFrame):
    """Fixed-height WebEngine host for a finalized Markdown document."""

    ready = pyqtSignal()

    def __init__(
        self,
        markdown: str,
        *,
        background: str | None = None,
        fast_render: bool = False,
        defer_show: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._markdown = str(markdown or "")
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

        view_cls = None if fast_render else try_create_webengine_view()
        if view_cls is None:
            self._view = self._build_fallback_browser()
            self._page_loaded = True
            self._defer_show = False
        else:
            est = estimate_markdown_height(self._markdown, width=640)
            self._view = view_cls(self)
            _configure_webengine_view(self._view)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
            self._apply_height(est)
            marked_src = marked_script_src()
            hljs_src = hljs_script_src()
            html = render_markdown_html(
                self._markdown,
                theme=markdown_theme_payload(background=self._background),
                marked_src=marked_src,
                hljs_src=hljs_src,
            )
            base_url = webengine_html_base(marked_src, hljs_src)
            self._view.setHtml(html, base_url)
            if hasattr(self._view, "page") and self._view.page() is not None:
                # Opaque page background keeps WebEngine in normal z-order on macOS;
                # transparent pages use a native layer that paints above overlays.
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
        browser.setHtml(render_markdown_safe(self._markdown))
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


def build_markdown_widget(
    markdown: str,
    *,
    background: str | None = None,
    fast_render: bool = False,
    defer_show: bool = False,
    parent=None,
) -> MarkdownWebWidget:
    return MarkdownWebWidget(
        markdown,
        background=background,
        fast_render=fast_render,
        defer_show=defer_show,
        parent=parent,
    )
