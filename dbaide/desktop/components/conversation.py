"""Codex-style conversation: question bubbles + answers, with a lightweight
"thinking" indicator per turn. The detailed agent trace lives in the right panel,
not inline — clicking a turn's indicator reveals it there."""

from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from dbaide.desktop.components.chart_block import ChartBlock
from dbaide.desktop.components.icon_button import IconToolButton

from PyQt6.QtCore import QSize

from dbaide.agent.progress_events import conversation_trace_step, phase_for
from dbaide.desktop.components.base import compact_button
from dbaide.desktop.conversation_state import ThinkingUiState, TurnTraceState
from dbaide.desktop.components.icons import svg_icon, svg_pixmap
from dbaide.desktop.components.inputs import configure_readonly_text_view, configure_wrapped_label
from dbaide.desktop.components.menu import _style_menu
from dbaide.desktop.components.spinner import BusyAnimator, SPINNER_SIZE, spinner_pixmap
from dbaide.desktop.components.trace import InlineTrace
from dbaide.desktop.theme import Theme

_TRACE_CHEVRON_SIZE = 15
_TRACE_ANIM_MS = 180
_TRACE_MAX_H = 340
from dbaide.charts.embed import split_answer_with_charts
from dbaide.rendering.markdown import render_markdown_safe


def _copy_to_clipboard(text: str) -> None:
    if text:
        QApplication.clipboard().setText(text)


def _selected_label_text(label: QLabel) -> str:
    return str(label.selectedText() or "").replace("\u2029", "\n")


def _selected_browser_text(browser: QTextBrowser) -> str:
    return str(browser.textCursor().selectedText() or "").replace("\u2029", "\n")


def _show_copy_menu(widget: QWidget, pos, *, selected_text: str, full_text: str) -> None:
    from dbaide.i18n import t

    menu = QMenu(widget)
    _style_menu(menu)
    selection_action = menu.addAction(t("message.copy_selection"))
    selection_action.setEnabled(bool(selected_text.strip()))
    selection_action.triggered.connect(lambda: _copy_to_clipboard(selected_text))
    message_action = menu.addAction(t("message.copy_message"))
    message_action.setEnabled(bool(full_text.strip()))
    message_action.triggered.connect(lambda: _copy_to_clipboard(full_text))
    menu.exec(widget.mapToGlobal(pos))


_FENCED_CODE_RE = re.compile(
    r"(?ms)^[ \t]{0,3}```([^\n`]*)\n(.*?)^[ \t]{0,3}```[ \t]*$"
)


def _split_fenced_code_blocks(markdown: str) -> list[tuple[str, str, str]]:
    """Split Markdown into prose/code chunks for UI affordances.

    Rendering prose and fenced blocks separately lets each code block have its own
    copy button while preserving the original Markdown for whole-message copy.
    Unclosed fences stay in the prose chunk so the renderer can show the text as-is.
    """
    text = str(markdown or "")
    parts: list[tuple[str, str, str]] = []
    pos = 0
    for match in _FENCED_CODE_RE.finditer(text):
        before = text[pos:match.start()]
        if before:
            parts.append(("markdown", before, ""))
        lang_parts = str(match.group(1) or "").strip().split(None, 1)
        lang = lang_parts[0] if lang_parts else ""
        code = str(match.group(2) or "")
        if code.endswith("\n"):
            code = code[:-1]
        if code.endswith("\r"):
            code = code[:-1]
        parts.append(("code", code, lang))
        pos = match.end()
    tail = text[pos:]
    if tail:
        parts.append(("markdown", tail, ""))
    return parts or [("markdown", text, "")]


class _AttachmentTags(QWidget):
    """Read-only, right-aligned row of attached db/table context tags shown above a
    user message (the schema itself is sent to the model, not echoed as text)."""

    def __init__(self, attachments: list[dict], parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        lay.addStretch(1)
        for att in attachments:
            kind = str(att.get("kind") or "table")
            name = str(att.get("name") or "")
            tag = QWidget()
            tag.setObjectName("msgTag")
            tag.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            tl = QHBoxLayout(tag)
            tl.setContentsMargins(8, 2, 9, 2)
            tl.setSpacing(5)
            icon = QLabel()
            icon.setPixmap(svg_icon("database" if kind == "database" else "table",
                                    color=Theme.BLUE, size=14).pixmap(QSize(14, 14)))
            icon.setFixedSize(14, 14)
            tl.addWidget(icon)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {Theme.TEXT_2}; font-size: 11px;")
            tl.addWidget(lbl)
            # Scope to #msgTag so the border doesn't cascade onto the icon/label.
            tag.setStyleSheet(
                f"QWidget#msgTag {{ background: {Theme.PANEL_2};"
                f" border: 1px solid {Theme.BORDER_SOFT}; border-radius: 8px; }}"
                f"QWidget#msgTag QLabel {{ background: transparent; border: none; }}"
            )
            tag.setFixedHeight(22)
            lay.addWidget(tag)


class _Bubble(QFrame):
    # Cap so very long questions don't stretch edge-to-edge; otherwise the bubble
    # sizes to its content (bounded by the available row width).
    MAX_W = 620

    def __init__(self, text: str, *, align_right: bool, parent=None) -> None:
        super().__init__(parent)
        # Fill the row; the bubble right/left-aligns its content-sized label itself.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._text = text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        # User text is shown verbatim as PLAIN text — no markup is interpreted, so it
        # is XSS-safe without HTML-escaping (escaping here would surface entities like
        # &#x27; literally, since the label is not a rich-text view).
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        label.customContextMenuRequested.connect(self._show_label_menu)
        label.setFont(QFont("Inter", 13))
        label.setStyleSheet(
            f"""
            background: {Theme.PANEL_2};
            color: {Theme.TEXT};
            border: 1px solid {Theme.BORDER_SOFT};
            border-radius: 8px;
            padding: 9px 14px;
            """
        )
        self._label = label
        if align_right:
            layout.addStretch(1)
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        else:
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
            layout.addStretch(1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        # Size the bubble to the longest line (so it's not a needlessly narrow column),
        # capped at MAX_W and never wider than the row — long text then wraps inside.
        super().resizeEvent(event)
        fm = self._label.fontMetrics()
        longest = max((fm.horizontalAdvance(line) for line in self._text.split("\n")), default=0)
        cap = min(self.MAX_W, max(140, self.width() - 8))
        # +44 covers the bubble's 16px horizontal padding each side, the border, and a
        # little metric jitter — so short text isn't wrapped a word early.
        self._label.setFixedWidth(max(48, min(cap, longest + 44)))

    def _show_label_menu(self, pos) -> None:
        _show_copy_menu(
            self._label,
            pos,
            selected_text=_selected_label_text(self._label),
            full_text=self._text,
        )

    def copy_message(self) -> None:
        _copy_to_clipboard(self._text)

    def copy_selection(self) -> None:
        _copy_to_clipboard(_selected_label_text(self._label))


class _ThinkingIndicator(QFrame):
    """Per-turn status chip. While the agent runs it shows a spinner + the current
    phase ("Thinking…", then phase labels); when done it collapses to a muted
    "View agent trace" link with a trailing chevron on the right. Clicking it
    expands the run's trace inline, right below the chip. Emits ``toggled_trace``."""

    toggled_trace = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("thinkingIndicator")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._state = ThinkingUiState()
        self._busy = BusyAnimator(self._tick, parent=self)
        self._hover = False
        self._tone = Theme.MUTED

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 10, 6)
        row.setSpacing(8)

        self._leading = QLabel()
        self._leading.setFixedSize(SPINNER_SIZE, SPINNER_SIZE)
        self._leading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._leading.hide()

        self._text = QLabel()
        self._text.setWordWrap(False)
        self._text.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._chevron = QLabel()
        self._chevron.setFixedSize(_TRACE_CHEVRON_SIZE, _TRACE_CHEVRON_SIZE)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.hide()

        for label in (self._leading, self._text, self._chevron):
            label.setAutoFillBackground(False)
            label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            label.setStyleSheet("background: transparent; border: none;")

        row.addWidget(self._leading, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._text, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sync_frame()
        self._sync()

    # ── state transitions ──────────────────────────────────────────────────--

    def start(self, phase: str = "") -> None:
        if not phase:
            from dbaide.i18n import t
            phase = t("status.thinking")
        self._state.start(phase)
        if not self._busy.active:
            self._busy.start()
        self._sync()

    def set_phase(self, phase: str) -> None:
        if not phase:
            return
        self._state.set_phase(phase)
        if not self._busy.active:
            self._busy.start()
        self._sync()

    def set_waiting(self, text: str = "") -> None:
        if not text:
            from dbaide.i18n import t
            text = t("status.waiting_reply")
        self._state.set_waiting(text)
        self._busy.stop()
        self._sync()

    def set_done(self, *, ok: bool, step_count: int, events: list[dict[str, Any]]) -> None:
        self._state.set_done(ok=ok, step_count=step_count, events=events)
        self._busy.stop()
        self._sync()

    # ── internals ──────────────────────────────────────────────────────────--

    def set_expanded(self, expanded: bool) -> None:
        self._state.set_expanded(expanded)
        self._sync()

    @property
    def _expanded(self) -> bool:
        return self._state.expanded

    def _on_click(self) -> None:
        self.toggled_trace.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)

    def sizeHint(self) -> QSize:
        if self.layout() is not None:
            return self.layout().sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self._hover = True
        self._sync_frame()
        if not self._state.running and not self._state.waiting:
            self._apply_tone(self._tone)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._sync_frame()
        if not self._state.running and not self._state.waiting:
            self._apply_tone(self._tone)
        super().leaveEvent(event)

    def _child_label_rule(self) -> str:
        return (
            "QFrame#thinkingIndicator QLabel {"
            " background: transparent; border: none; padding: 0; margin: 0; }"
        )

    def _sync_frame(self) -> None:
        state = self._state
        idle_done = not state.running and not state.waiting and state.step_count > 0
        child = self._child_label_rule()
        if self._hover and idle_done:
            self.setStyleSheet(
                f"QFrame#thinkingIndicator {{ background: {Theme.PANEL_2};"
                f" border: 1px solid {Theme.BORDER_SOFT}; border-radius: {Theme.RADIUS_MD}px; }}"
                f"{child}"
            )
        elif idle_done:
            # Quiet pill at rest — matches hover surface so the chevron never sits on
            # a different fill than the label (macOS QLabel pixmap boxes read as black).
            self.setStyleSheet(
                f"QFrame#thinkingIndicator {{ background: {Theme.PANEL_2};"
                f" border: 1px solid transparent; border-radius: {Theme.RADIUS_MD}px; }}"
                f"{child}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#thinkingIndicator {{ background: transparent; border: none; }}"
                f"{child}"
            )

    def _text_style(self, color: str) -> str:
        return (
            f"color: {color}; background: transparent; border: none;"
            f" font-size: 11px; font-weight: 600;"
        )

    def _fit_text_width(self, *, max_width: int = 0) -> None:
        """Size the label to its rendered string — global QSS font-size otherwise
        makes QLabel sizeHint too narrow and the last glyphs clip under the chevron."""
        text = self._text.text()
        if not text:
            return
        fm = self._text.fontMetrics()
        pad = 8
        natural = fm.horizontalAdvance(text) + pad
        width = min(natural, max_width) if max_width > 0 else natural
        if max_width > 0 and natural > max_width:
            self._text.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, max_width - pad))
            width = max_width
        self._text.setFixedWidth(width)

    def _tick(self) -> None:
        if not self._state.running:
            return
        self._leading.setPixmap(spinner_pixmap(self._busy.angle, color=Theme.BLUE, size=SPINNER_SIZE))

    def _set_chevron(self, *, expanded: bool, color: str) -> None:
        name = "chevron-down" if expanded else "chevron-right"
        self._chevron.setPixmap(svg_pixmap(name, color=color, size=_TRACE_CHEVRON_SIZE))

    def _apply_tone(self, color: str) -> None:
        self._tone = color
        show_hover = self._hover and not self._state.running and not self._state.waiting
        display = Theme.TEXT if show_hover else color
        self._text.setStyleSheet(self._text_style(display))
        if not self._state.running and not self._state.waiting and self._state.step_count > 0:
            self._set_chevron(expanded=self._state.expanded, color=display)
            self._chevron.setStyleSheet("background: transparent; border: none;")
            self._fit_text_width()

    def _sync(self) -> None:
        state = self._state
        if state.running:
            phase = state.phase if state.phase.endswith("…") else f"{state.phase}…"
            self._leading.show()
            self._chevron.hide()
            self._text.setText(phase)
            self._apply_tone(Theme.BLUE)
            self._fit_text_width(max_width=420)
            self._tick()
            self.show()
        elif state.waiting:
            self._leading.hide()
            self._chevron.hide()
            self._text.setText(state.phase)
            self._apply_tone(Theme.YELLOW)
            self._fit_text_width(max_width=420)
            self.show()
        else:
            if state.step_count <= 0:
                self.hide()
                return
            from dbaide.i18n import t
            base = t("trace.view") if state.ok else t("trace.view_failed")
            self._leading.hide()
            self._chevron.show()
            self._text.setText(base)
            self._apply_tone(Theme.MUTED if state.ok else Theme.RED)
            self._sync_frame()
            self._fit_text_width()
            self.show()
        self.adjustSize()
        self.updateGeometry()


class _CodeBlock(QFrame):
    """Standalone fenced-code block with a compact copy action."""

    def __init__(self, code: str, *, language: str = "", parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self._code = str(code or "")
        self._language = str(language or "").strip()
        self.setObjectName("answerCodeBlock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"""
            QFrame#answerCodeBlock {{
                background: {Theme.CODE_BG};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 8px;
            }}
            QPlainTextEdit {{
                background: transparent;
                color: {Theme.TEXT};
                border: none;
                padding: 0;
                selection-background-color: {Theme.PANEL_3};
                font-family: Menlo, Monaco, Consolas, monospace;
                font-size: 12px;
            }}
            QLabel {{
                background: transparent;
                color: {Theme.MUTED};
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 7, 10, 10)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        label = QLabel(self._language.upper() if self._language else t("conversation.code"))
        header.addWidget(label)
        header.addStretch(1)
        self._copy_btn = IconToolButton(
            svg_icon("copy", color=Theme.MUTED, size=12),
            t("message.copy_code"),
        )
        self._copy_btn.clicked.connect(self.copy_code)
        header.addWidget(self._copy_btn)
        outer.addLayout(header)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(self._code)
        self._editor.setReadOnly(True)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._editor.customContextMenuRequested.connect(self._show_editor_menu)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        line_count = max(1, self._code.count("\n") + 1)
        self._editor.setFixedHeight(min(320, max(50, line_count * 18 + 10)))
        outer.addWidget(self._editor)

    def update_code(self, code: str, *, language: str = "") -> None:
        self._code = str(code or "")
        self._editor.setPlainText(self._code)
        line_count = max(1, self._code.count("\n") + 1)
        self._editor.setFixedHeight(min(320, max(50, line_count * 18 + 10)))

    def copy_code(self) -> None:
        from dbaide.i18n import t

        QApplication.clipboard().setText(self._code)
        self._copy_btn.setIcon(svg_icon("check", color=Theme.GREEN, size=12))
        self._copy_btn.setToolTip(t("ask.copied"))

        def restore() -> None:
            try:
                self._copy_btn.setIcon(svg_icon("copy", color=Theme.MUTED, size=12))
                self._copy_btn.setToolTip(t("message.copy_code"))
            except RuntimeError:
                pass

        QTimer.singleShot(1200, restore)

    def _show_editor_menu(self, pos) -> None:
        _show_copy_menu(
            self._editor.viewport(),
            pos,
            selected_text=str(self._editor.textCursor().selectedText() or "").replace("\u2029", "\n"),
            full_text=self._code,
        )


class _MarkdownBlock(QFrame):
    """A rendered-markdown chunk in the conversation. By default it flows directly
    on the background (no card) — the assistant's answer reads like prose, the way
    Claude/Cursor present it. Pass ``boxed=True`` for set-apart content (warnings,
    errors) that deserves a subtle inset card; ``accent`` tints that card's edge."""

    def __init__(self, markdown: str, *, title: str = "", boxed: bool = False,
                 accent: str = "", title_tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self._markdown = str(markdown or "")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("answerBlock")
        if boxed:
            self.setStyleSheet(
                f"QFrame#answerBlock {{ background: {Theme.PANEL};"
                f" border: 1px solid {accent or Theme.BORDER_SOFT}; border-radius: 8px; }}"
            )
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 12, 16, 12)
        else:
            self.setStyleSheet("QFrame#answerBlock { background: transparent; border: none; }")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if title:
            t = QLabel(title)
            t.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            t.setStyleSheet(
                f"color: {accent or Theme.MUTED}; background: transparent; letter-spacing: 0.3px;"
            )
            if title_tooltip:
                t.setToolTip(title_tooltip)
            layout.addWidget(t)
        self._content_layout = layout
        self._browsers: list[QTextBrowser] = []
        self._code_blocks: list[_CodeBlock] = []
        self._body: QTextBrowser | None = None
        self._render_segments()

    def set_markdown(self, markdown: str) -> None:
        """Re-render the body (used by the progressive answer reveal)."""
        self._markdown = str(markdown or "")
        new_segments = _split_fenced_code_blocks(self._markdown)
        if self._can_update_in_place(new_segments):
            self._update_in_place(new_segments)
        else:
            self._render_segments()
        self._sync_body_height()

    def _can_update_in_place(self, new_segments: list[tuple[str, str, str]]) -> bool:
        """Check if we can update existing widgets instead of rebuilding."""
        old_kinds = [("browser" if not isinstance(w, _CodeBlock) else "code")
                     for w in self._browsers + self._code_blocks]
        new_kinds = [("code" if k == "code" else "browser")
                     for k, p, _ in new_segments if k == "code" or p.strip()]
        return bool(old_kinds) and old_kinds == new_kinds

    def _update_in_place(self, segments: list[tuple[str, str, str]]) -> None:
        """Update existing widget contents without destroying/recreating them."""
        bi, ci = 0, 0
        for kind, payload, meta in segments:
            if kind == "code":
                if ci < len(self._code_blocks):
                    self._code_blocks[ci].update_code(payload, language=meta)
                ci += 1
            elif payload.strip():
                if bi < len(self._browsers):
                    self._browsers[bi].setHtml(render_markdown_safe(payload))
                bi += 1

    def _make_text_browser(self, markdown: str) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        browser.customContextMenuRequested.connect(lambda pos, b=browser: self._show_body_menu(b, pos))
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setFont(QFont("Inter", 13))
        configure_readonly_text_view(browser)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        browser.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; color: {Theme.TEXT}; padding: 0; }}"
        )
        from dbaide.desktop.components.md_css import markdown_stylesheet
        browser.document().setDefaultStyleSheet(markdown_stylesheet())
        browser.setHtml(render_markdown_safe(markdown))
        browser.document().documentLayout().documentSizeChanged.connect(self._sync_body_height)
        return browser

    def _render_segments(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(self._content_layout.count() - 1)
            widget = item.widget()
            if widget is not None and not isinstance(widget, QLabel):
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif widget is not None:
                self._content_layout.insertWidget(0, widget)
                break
        self._browsers = []
        self._code_blocks = []
        self._body = None
        for kind, payload, meta in _split_fenced_code_blocks(self._markdown):
            if kind == "code":
                code = _CodeBlock(payload, language=meta)
                self._code_blocks.append(code)
                self._content_layout.addWidget(code)
                continue
            if not payload.strip():
                continue
            browser = self._make_text_browser(payload)
            self._browsers.append(browser)
            if self._body is None:
                self._body = browser
            self._content_layout.addWidget(browser)
        self._sync_body_height()

    def _show_body_menu(self, browser: QTextBrowser, pos) -> None:
        _show_copy_menu(
            browser.viewport(),
            pos,
            selected_text=_selected_browser_text(browser),
            full_text=self._markdown or browser.toPlainText(),
        )

    def copy_message(self) -> None:
        fallback = self._body.toPlainText() if self._body is not None else ""
        _copy_to_clipboard(self._markdown or fallback)

    def copy_selection(self) -> None:
        for browser in self._browsers:
            selected = _selected_browser_text(browser)
            if selected.strip():
                _copy_to_clipboard(selected)
                return

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_body_height()

    def _sync_body_height(self, *_args) -> None:
        for browser in self._browsers:
            doc = browser.document()
            width = max(browser.viewport().width(), self.width() - 32, 320)
            doc.setTextWidth(width)
            height = int(doc.documentLayout().documentSize().height()) + 8
            browser.setFixedHeight(max(height, 24))


class _ClarificationOption(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._value = text
        self.setObjectName("clarificationOption")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(34)
        self.setStyleSheet(
            f"""
            QFrame#clarificationOption {{
                background: {Theme.PANEL_2};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 7px;
            }}
            QFrame#clarificationOption:hover {{
                background: {Theme.PANEL_3};
                border-color: {Theme.BORDER};
            }}
            QFrame#clarificationOption QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(0)
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setStyleSheet(f"color: {Theme.TEXT}; font-size: 13px;")
        configure_wrapped_label(self.label)
        layout.addWidget(self.label, 1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._value)
        super().mouseReleaseEvent(event)


class _ClarificationBar(QFrame):
    """Reply controls for a clarification: full-width wrapped option rows plus an
    inline free-text input + Send. When there are several questions an option only
    answers one, so it fills the input instead of submitting immediately."""

    submitted = pyqtSignal(str)

    def __init__(self, options: list[str], *, allow_direct_submit: bool = True, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self.setObjectName("clarificationBar")
        self.setStyleSheet(
            f"""
            QFrame#clarificationBar {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 8px;
            }}
            """
        )
        self._direct = allow_direct_submit
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)
        self._option_rows: list[_ClarificationOption] = []

        if options:
            options_host = QWidget()
            options_host.setStyleSheet("background: transparent;")
            options_layout = QVBoxLayout(options_host)
            options_layout.setContentsMargins(0, 0, 0, 0)
            options_layout.setSpacing(6)
            for option in options:
                row_widget = _ClarificationOption(option)
                row_widget.clicked.connect(self._on_chip)
                self._option_rows.append(row_widget)
                options_layout.addWidget(row_widget)
            outer.addWidget(options_host)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText(
            t("clarify.type_answer") if allow_direct_submit else t("clarify.type_multi")
        )
        self._input.setFixedHeight(26)
        self._input.returnPressed.connect(self._on_send)
        row.addWidget(self._input, 1)
        self._send = compact_button(t("composer.send"), primary=True, width=72)
        self._send.clicked.connect(self._on_send)
        row.addWidget(self._send)
        outer.addLayout(row)

    def _on_chip(self, value: str) -> None:
        if self._direct:
            self.submitted.emit(value)
            return
        # Multiple questions: assemble the answer in the input rather than submit one.
        existing = self._input.text().strip()
        self._input.setText(f"{existing}; {value}" if existing else value)
        self._input.setFocus()

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if text:
            self.submitted.emit(text)


class _ClarificationStepper(QFrame):
    """Multi-question clarification answered ONE question at a time. Each step shows
    a single question, its option rows, and a free-text input; picking an option
    (or typing + Next) records the answer and advances. After the last question the
    answers are assembled into a single numbered reply and submitted — so the agent
    still asks several things at once, but the user answers them sequentially."""

    submitted = pyqtSignal(str)

    def __init__(self, questions: list[dict], parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self._t = t
        self._questions = questions
        self._idx = 0
        self._answers: list[str] = []
        self.setObjectName("clarificationBar")
        self.setStyleSheet(
            f"QFrame#clarificationBar {{ background: {Theme.PANEL};"
            f" border: 1px solid {Theme.BORDER_SOFT}; border-radius: 8px; }}"
        )
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(12, 10, 12, 12)
        self._outer.setSpacing(8)

        self._progress = QLabel("")
        self._progress.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600;")
        self._outer.addWidget(self._progress)
        self._ask = QLabel("")
        self._ask.setWordWrap(True)
        self._ask.setStyleSheet(f"color: {Theme.TEXT}; font-size: 13px;")
        self._outer.addWidget(self._ask)

        self._options_host = QWidget()
        self._options_host.setStyleSheet("background: transparent;")
        self._options = QVBoxLayout(self._options_host)
        self._options.setContentsMargins(0, 0, 0, 0)
        self._options.setSpacing(6)
        self._option_rows: list[_ClarificationOption] = []
        self._outer.addWidget(self._options_host)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._back = compact_button(t("clarify.back"), width=64)
        self._back.clicked.connect(self._on_back)
        row.addWidget(self._back)
        self._input = QLineEdit()
        self._input.setFixedHeight(26)
        self._input.returnPressed.connect(self._on_next)
        row.addWidget(self._input, 1)
        self._next = compact_button("", primary=True, width=84)
        self._next.clicked.connect(self._on_next)
        row.addWidget(self._next)
        self._outer.addLayout(row)

        self._render()

    def _render(self) -> None:
        q = self._questions[self._idx]
        total = len(self._questions)
        self._progress.setText(self._t("clarify.progress", current=self._idx + 1, total=total))
        self._ask.setText(str(q.get("ask") or ""))
        # rebuild option rows for this question's options
        while self._options.count():
            item = self._options.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        opts = [str(o) for o in (q.get("options") or []) if str(o).strip()]
        self._option_rows = []
        self._options_host.setVisible(bool(opts))
        for opt in opts:
            row_widget = _ClarificationOption(opt)
            row_widget.clicked.connect(self._answer)
            self._option_rows.append(row_widget)
            self._options.addWidget(row_widget)
        # restore any previously-entered answer for this step
        self._input.setText(self._answers[self._idx] if self._idx < len(self._answers) else "")
        self._input.setPlaceholderText(self._t("clarify.type_answer"))
        self._back.setVisible(self._idx > 0)
        last = self._idx == total - 1
        self._next.setText(self._t("clarify.finish") if last else self._t("clarify.next"))
        self._next.setIcon(svg_icon("check" if last else "chevron-right", color=Theme.ACCENT, size=14))
        self._input.setFocus()

    def _record_current(self, value: str) -> None:
        if self._idx < len(self._answers):
            self._answers[self._idx] = value
        else:
            self._answers.append(value)

    def _answer(self, value: str) -> None:
        # An option row both fills and advances.
        self._record_current(value)
        self._advance()

    def _on_next(self) -> None:
        value = self._input.text().strip()
        if not value:
            return  # require an answer (the agent re-asks anything left open anyway)
        self._record_current(value)
        self._advance()

    def _on_back(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._render()

    def _advance(self) -> None:
        if self._idx < len(self._questions) - 1:
            self._idx += 1
            self._render()
            return
        # Assemble a numbered reply mapping each question to its answer.
        lines = []
        for i, q in enumerate(self._questions):
            ans = self._answers[i] if i < len(self._answers) else ""
            lines.append(f"{i + 1}. {ans}")
        self.submitted.emit("\n".join(lines))


class TurnBlock(QFrame):
    """One complete Q&A turn in a single scroll block."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("turnBlock")
        self.setStyleSheet("QFrame#turnBlock { background: transparent; border: none; }")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 4)
        self._layout.setSpacing(12)

        self._header = QWidget()
        self._header.setStyleSheet("background: transparent;")
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._header_layout = QVBoxLayout(self._header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(6)
        self._header.hide()
        self._layout.addWidget(self._header)

        # Lightweight per-turn status (spinner while thinking, then a "view trace"
        # link). Clicking it expands this turn's trace inline, just below the chip.
        self.status = _ThinkingIndicator()
        self.status.toggled_trace.connect(self._toggle_trace)
        self._layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)

        # The inline trace is created lazily on first expand (most turns are never
        # expanded — no point building a tree widget for each).
        self.trace_state = TurnTraceState()
        self._trace_box: InlineTrace | None = None
        self._trace_anim: QPropertyAnimation | None = None

        self._content_host = QWidget()
        self._content_host.setStyleSheet("background: transparent;")
        self._content_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._content = QVBoxLayout(self._content_host)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(10)
        self._content_host.hide()
        self._layout.addWidget(self._content_host)

        # Footer row: stats on the left, action buttons on the right.
        self._footer = QWidget()
        self._footer.setStyleSheet("background: transparent;")
        self._footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._footer_layout = QHBoxLayout(self._footer)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(4)
        self._stats_label = QLabel()
        self._stats_label.setStyleSheet(
            f"color: {Theme.MUTED_2}; background: transparent;"
            f" font-size: 10px;"
        )
        self._footer_layout.addWidget(self._stats_label)
        self._footer_layout.addStretch(1)
        self._footer_actions: QHBoxLayout | None = None
        self._footer.hide()
        self._layout.addWidget(self._footer)
        self._trace_model_cache: "TraceModel | None" = None

    def set_user(self, text: str, *, meta: str = "", attachments: list[dict] | None = None) -> None:
        self._header.show()
        if meta:
            meta_label = QLabel(meta)
            meta_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            meta_label.setFont(QFont("Inter", 10))
            meta_label.setStyleSheet(f"color: {Theme.MUTED_2}; background: transparent;")
            self._header_layout.addWidget(meta_label)
        # Attached schema context shows as compact, right-aligned tags above the
        # bubble (GPT-style) — the schema itself is sent to the model, not echoed
        # into the visible message text.
        if attachments:
            self._header_layout.addWidget(_AttachmentTags(attachments))
        self._header_layout.addWidget(_Bubble(text, align_right=True))

    def append_content(self, widget: QWidget) -> None:
        self._content_host.show()
        self._content.addWidget(widget)

    def remove_content_widget(self, widget: QWidget) -> None:
        """Drop a widget from the answer column (e.g. replace streamed prose with embeds)."""
        self._content.removeWidget(widget)
        widget.setParent(None)
        widget.deleteLater()

    # ── inline trace ───────────────────────────────────────────────────────────

    def _trace_open(self) -> bool:
        # Explicit show/hide state — independent of ancestor visibility (isVisible()
        # is False whenever the conversation isn't on screen, which would break the
        # toggle and live-feed checks).
        return self._trace_box is not None and not self._trace_box.isHidden()

    def add_live_event(self, event: dict[str, Any]) -> None:
        """Accumulate a streamed event; feed the inline trace if it's open."""
        self.trace_state.append(event)
        if self._trace_open():
            self._trace_box.append_live_event(event)
        self._ingest_live_stats(event)

    def set_trace(self, events: list[dict[str, Any]]) -> None:
        """Final, authoritative trace for this turn (from the persisted result)."""
        self.trace_state.set_final(events)
        if self._trace_open():
            self._trace_box.set_events(self.trace_state.events, live=False)
        self._rebuild_stats(events)

    def _ingest_live_stats(self, event: dict[str, Any]) -> None:
        """Feed a single event to the cached trace model for realtime display."""
        from dbaide.agent.trace_model import TraceModel
        if self._trace_model_cache is None:
            self._trace_model_cache = TraceModel()
        if isinstance(event, dict):
            self._trace_model_cache.ingest(event)
        self._render_stats(self._trace_model_cache)

    def _rebuild_stats(self, events: list[dict[str, Any]]) -> None:
        from dbaide.agent.trace_model import TraceModel
        model = TraceModel()
        for ev in events or []:
            if isinstance(ev, dict):
                model.ingest(ev)
        model.finalize()
        self._trace_model_cache = model
        self._render_stats(model)

    def _render_stats(self, model: "TraceModel") -> None:
        from dbaide.agent.trace_model import _format_tokens
        from dbaide.i18n import t
        steps = len(model.steps)
        if steps <= 0 and model.overall in ("idle",):
            return
        elapsed = model.elapsed_ms() / 1000.0
        parts: list[str] = []
        if steps > 0:
            parts.append(t("trace.steps", n=steps))
        if elapsed >= 0.1:
            parts.append(f"{elapsed:.1f}s")
        tokens = _format_tokens(model.prompt_tokens)
        if tokens:
            parts.append(tokens)
        if parts:
            self._stats_label.setText(" · ".join(parts))
            self._footer.show()

    def set_actions(self, widget: QWidget | None) -> None:
        """Set the action buttons (copy answer, copy CLI) in the footer row."""
        if self._footer_actions is not None:
            while self._footer_actions.count():
                item = self._footer_actions.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        if widget is None:
            return
        if self._footer_actions is None:
            self._footer_actions = QHBoxLayout()
            self._footer_actions.setContentsMargins(0, 0, 0, 0)
            self._footer_actions.setSpacing(2)
            self._footer_layout.addLayout(self._footer_actions)
        self._footer_actions.addWidget(widget)
        self._footer.show()

    def _toggle_trace(self) -> None:
        if self._trace_box is None:
            self._trace_box = InlineTrace()
            idx = self._layout.indexOf(self.status)
            self._layout.insertWidget(idx + 1, self._trace_box)
            self._trace_box.hide()
            self._trace_box.setMaximumHeight(0)
        if self._trace_open():
            self._animate_trace(opening=False)
            self.status.set_expanded(False)
        else:
            self._trace_box.set_events(self.trace_state.events, live=not self.trace_state.final)
            self._animate_trace(opening=True)
            self.status.set_expanded(True)

    def _animate_trace(self, *, opening: bool) -> None:
        box = self._trace_box
        if box is None:
            return
        end = _TRACE_MAX_H if opening else 0
        if opening:
            box.show()
        anim = QPropertyAnimation(box, b"maximumHeight", self)
        anim.setDuration(_TRACE_ANIM_MS)
        anim.setStartValue(box.maximumHeight())
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic if opening else QEasingCurve.Type.InCubic)

        def _on_finished() -> None:
            if opening:
                box.setMaximumHeight(_TRACE_MAX_H)
            else:
                box.hide()
                box.setMaximumHeight(_TRACE_MAX_H)

        anim.finished.connect(_on_finished)
        anim.start()
        self._trace_anim = anim

    @property
    def _events(self) -> list[dict[str, Any]]:
        return self.trace_state.events

    @_events.setter
    def _events(self, value: list[dict[str, Any]]) -> None:
        self.trace_state.events = list(value or [])

    @property
    def _trace_final(self) -> bool:
        return self.trace_state.final

    @_trace_final.setter
    def _trace_final(self, value: bool) -> None:
        self.trace_state.final = bool(value)


class ConversationView(QScrollArea):
    # A FIXED side margin at every window size — the gap to the edges stays the same
    # whether the window is small or fullscreen. (A centred max-width column would
    # instead grow the side gutters as the window widens, which reads as the spacing
    # "ballooning" on large/fullscreen windows.) User bubbles still cap their own
    # width and hug the right; only the assistant's text uses the full column.
    _H_MARGIN = 28

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"QScrollArea {{ border: none; background: {Theme.BG}; }}")

        self._root = QWidget()
        self._root.setStyleSheet(f"background: {Theme.BG};")
        self._root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._layout = QVBoxLayout(self._root)
        self._layout.setContentsMargins(self._H_MARGIN, 16, self._H_MARGIN, 24)
        self._layout.setSpacing(16)
        # Top stretch anchors conversation turns to the bottom (chat-style).
        self._layout.addStretch(1)
        self.setWidget(self._root)
        self._current_turn: TurnBlock | None = None
        self._hint_label: QLabel | None = None
        self._last_meta = ""  # last shown user-meta caption (to skip repeats)
        # Retained per-turn records (question, trace events, answer) for "copy the
        # whole conversation's trace".
        self._turns: list[dict[str, Any]] = []
        self._current_record: dict[str, Any] | None = None
        self._clarification_bar: _ClarificationBar | None = None
        # True token-streaming: the answer block being filled live by answer_chunk
        # events for the open turn (None until the first chunk arrives). There is no
        # front-end simulation — if the model can't stream, the answer simply renders
        # once at complete_turn.
        self._live_answer: "_MarkdownBlock | None" = None
        self._live_answer_text = ""
        self._chunk_dirty = False
        self._chunk_timer = QTimer(self)
        self._chunk_timer.setSingleShot(True)
        self._chunk_timer.setInterval(80)
        self._chunk_timer.timeout.connect(self._flush_answer_chunk)

    def append_answer_chunk(self, text: str) -> None:
        """Append a streamed slice of the final answer to the open turn, creating the
        answer block on the first chunk. This is real token-streaming (the model is
        still generating); ``complete_turn`` later snaps it to the authoritative text."""
        if not text or self._current_turn is None:
            return
        if self._live_answer is None:
            self._live_answer = _MarkdownBlock("", title="DBAide")
            self._current_turn.append_content(self._live_answer)
        self._live_answer_text += text
        if not self._chunk_dirty:
            self._chunk_dirty = True
            self._chunk_timer.start()

    def _flush_answer_chunk(self) -> None:
        self._chunk_dirty = False
        if self._live_answer is None:
            return
        try:
            self._live_answer.set_markdown(self._live_answer_text)
        except RuntimeError:
            self._live_answer = None
            return
        self._scroll_bottom()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_viewport_width()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_viewport_width()

    def _sync_viewport_width(self) -> None:
        """QScrollArea keeps content width when height overflows; force full viewport width."""
        viewport_w = self.viewport().width()
        if viewport_w <= 0:
            return
        self._root.setMinimumWidth(viewport_w)
        # Constant side margins regardless of width (no centred cap) → the spacing to
        # the edges never changes as the window resizes.
        side = self._H_MARGIN
        self._layout.setContentsMargins(side, 16, side, 24)
        content_w = max(200, viewport_w - side * 2)
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setMinimumWidth(content_w)

    def begin_turn(self, user_text: str, *, meta: str = "", placeholder: bool = True,
                   attachments: list[dict] | None = None) -> None:
        self._chunk_timer.stop()
        self._chunk_dirty = False
        self._live_answer = None
        self._live_answer_text = ""
        turn = TurnBlock()
        if user_text.strip():
            # Only surface the connection · db caption when it changes from the
            # previous turn — repeating unchanged context on every message is noise.
            show_meta = meta if (meta and meta != self._last_meta) else ""
            if meta:
                self._last_meta = meta
            turn.set_user(user_text, meta=show_meta, attachments=attachments)
        self._insert_turn(turn)
        self._current_turn = turn
        self._current_record = {"question": user_text, "events": [], "answer": ""}
        self._turns.append(self._current_record)
        # placeholder=True: a live run → spin immediately. placeholder=False: a
        # restored turn → stays idle until complete_turn sets its "view trace" link.
        if placeholder:
            turn.status.start()
            self._seed_live_trace_boot(turn)
        self._scroll_bottom()

    def append_trace(self, message: str, *, kind: str = "", detail: str = "") -> None:
        if self._current_turn is None:
            self.begin_turn("")
        if self._current_turn is None:
            return
        if message.strip():
            self._current_turn.status.set_phase(message.strip())
        self._scroll_bottom()

    def append_trace_event(self, event: dict[str, Any]) -> None:
        if self._current_turn is None:
            self.begin_turn("")
        if self._current_turn is None:
            return
        # Surface the current phase on the thinking chip (a friendly label like
        # "Linking schema"); the full detail goes to the right panel.
        phase = phase_for(str(event.get("stage") or ""))
        if not phase:
            step = conversation_trace_step(event)
            phase = step[0] if step else ""
        if phase:
            self._current_turn.status.set_phase(phase)
        if self._current_record is not None:
            self._current_record["events"].append(event)
        self._current_turn.add_live_event(event)
        self._scroll_bottom()

    def append_clarification(self, *, question: str, options: list[str],
                             questions: list[dict] | None = None):
        if self._current_turn is None:
            self.begin_turn("")
        turn = self._current_turn
        if turn is None:
            return None
        turn.status.set_waiting()
        structured = [q for q in (questions or []) if str(q.get("ask") or "").strip()]
        if len(structured) > 1:
            # Several questions → step through them one at a time. A compact header
            # replaces the full numbered blob (each question is shown per step).
            turn.append_content(_MarkdownBlock(
                f"**{self._tr('clarify.title')}**", title="DBAide"))
            bar = _ClarificationStepper(structured)
        else:
            # Single question → the question text + its option chips, submit directly.
            turn.append_content(_MarkdownBlock(f"**{self._tr('clarify.title')}**\n\n{question}", title="DBAide"))
            single_opts = (structured[0].get("options") if structured else None) or options
            bar = _ClarificationBar([str(o) for o in single_opts if str(o).strip()],
                                    allow_direct_submit=True)
        turn.append_content(bar)
        self._clarification_bar = bar
        self._scroll_bottom()
        return bar

    @staticmethod
    def _tr(key: str) -> str:
        from dbaide.i18n import t
        return t(key)

    def _seed_live_trace_boot(self, turn: TurnBlock) -> None:
        """Prime the trace before the worker thread emits events (connection check, …)."""
        from dbaide.agent.progress_events import progress_event

        boot = progress_event(
            stage="environment_check",
            title=self._tr("trace.phase.environment_check"),
            status="running",
            kind="phase",
            node_id="workflow:environment_check",
        )
        turn.add_live_event(boot)
        if self._current_record is not None:
            self._current_record["events"].append(boot)

    def _append_answer_with_embedded_charts(
        self,
        turn: TurnBlock,
        answer: str,
        charts: list[dict[str, Any]] | None,
        *,
        workflow_id: str = "",
        replace_widget: _MarkdownBlock | None = None,
    ) -> None:
        """Render answer prose and charts in document order (inline placeholders)."""
        from dbaide.charts.embed import CHART_EMBED_RE

        body = str(answer or "")
        chart_list = [c for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
        has_embeds = bool(CHART_EMBED_RE.search(body))
        if (
            replace_widget is not None
            and not chart_list
            and not has_embeds
            and body.strip()
        ):
            try:
                replace_widget.set_markdown(body)
            except RuntimeError:
                turn.remove_content_widget(replace_widget)
                turn.append_content(_MarkdownBlock(
                    body,
                    title="DBAide",
                    title_tooltip=f"workflow {workflow_id}" if workflow_id else "",
                ))
            return

        if replace_widget is not None:
            turn.remove_content_widget(replace_widget)

        if not body.strip() and not chart_list:
            return

        segments = split_answer_with_charts(body, chart_list)
        if not segments and body.strip():
            segments = [("md", body)]

        first_md = True
        for kind, payload in segments:
            if kind == "md":
                turn.append_content(_MarkdownBlock(
                    str(payload),
                    title="DBAide" if first_md else "",
                    title_tooltip=f"workflow {workflow_id}" if first_md and workflow_id else "",
                ))
                first_md = False
            elif kind == "chart" and isinstance(payload, dict):
                turn.append_content(ChartBlock(payload))

    def append_clarification_reply(self, text: str) -> None:
        if self._current_turn is None:
            return
        # The choice is made — retract the (now stale) option chips so the prompt
        # doesn't keep hanging there as if it still wants an answer.
        if self._clarification_bar is not None:
            self._clarification_bar.hide()
            self._clarification_bar = None
        self._current_turn._header.show()
        self._current_turn._header_layout.addWidget(_Bubble(text, align_right=True))
        self._scroll_bottom()

    def complete_turn(
        self,
        *,
        answer: str = "",
        trace_events: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        workflow_id: str = "",
        ok: bool = True,
        actions_widget: QWidget | None = None,
        charts: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._current_turn is None:
            self.begin_turn("")
        turn = self._current_turn
        if turn is None:
            return
        # The persisted trace is the authoritative one; fall back to whatever streamed
        # in live. These events feed the right panel when the chip is clicked.
        events = list(trace_events) if trace_events else list(
            (self._current_record or {}).get("events") or []
        )
        if self._current_record is not None:
            if trace_events:
                self._current_record["events"] = list(trace_events)
            self._current_record["answer"] = answer
            if charts:
                self._current_record["charts"] = list(charts)
        turn.status.set_done(ok=ok, step_count=len(events), events=events)
        # Hand the authoritative trace to the turn so its inline view (if/when the
        # user expands the chip) shows the finalized run, not just what streamed.
        turn.set_trace(events)

        # Clean author label — just "DBAide" (the internal workflow id is noise in the
        # message header, Codex-style; keep it reachable as a tooltip and in the trace).
        self._chunk_timer.stop()
        if self._chunk_dirty:
            self._flush_answer_chunk()
        live = self._live_answer
        live_text = self._live_answer_text
        self._live_answer = None
        self._live_answer_text = ""
        self._append_answer_with_embedded_charts(
            turn,
            answer or live_text,
            charts,
            workflow_id=workflow_id,
            replace_widget=live,
        )
        if actions_widget is not None:
            turn.set_actions(actions_widget)
        notes: list[str] = []
        if warnings:
            notes.append(f"**{self._tr('conversation.warnings')}**\n" + "\n".join(f"- {w}" for w in warnings))
        if errors:
            lines = []
            for err in errors:
                if isinstance(err, dict):
                    lines.append(f"- [{err.get('stage', '')}] {err.get('message', '')}")
                else:
                    lines.append(f"- {err}")
            notes.append(f"**{self._tr('conversation.notes')}**\n" + "\n".join(lines))
        if notes:
            turn.append_content(_MarkdownBlock("\n\n".join(notes), boxed=True))

        self._current_turn = None
        self._current_record = None
        self._scroll_bottom()

    def copy_text(self) -> str:
        """Export the whole conversation: each turn's question, structured trace and
        answer, separated. Used by 'Copy conversation'."""
        from dbaide.agent.trace_model import render_events_text

        blocks: list[str] = []
        n = 0
        for rec in self._turns:
            q = str(rec.get("question") or "").strip()
            ans = str(rec.get("answer") or "").strip()
            trace = render_events_text(rec.get("events") or [])
            if not (q or ans or trace):
                continue
            n += 1
            parts = [f"### Turn {n}"]
            if q:
                parts.append(f"Q: {q}")
            if trace:
                parts += ["", "Trace:", trace]
            if ans:
                parts += ["", "Answer:", ans]
            blocks.append("\n".join(parts))
        return ("\n\n" + "─" * 60 + "\n\n").join(blocks)

    def has_open_turn(self) -> bool:
        """True while a turn is mid-run (thinking / awaiting reply) — i.e. this
        session has an in-flight conversation."""
        return self._current_turn is not None

    def finish_turn_error(self, message: str) -> None:
        if self._current_turn:
            events = list((self._current_record or {}).get("events") or [])
            self._current_turn.status.set_done(ok=False, step_count=len(events), events=events)
            self._current_turn.append_content(
                _MarkdownBlock(message, title="Error", boxed=True, accent=Theme.RED)
            )
            if self._current_record is not None:
                self._current_record["answer"] = message
            self._current_turn = None
            self._current_record = None
        else:
            self.begin_turn("")
            self.complete_turn(answer=message, ok=False)

    def append_hint(self, text: str) -> None:
        if self._hint_label is not None:
            self._hint_label.setText(text)
            return
        label = QLabel(text)
        configure_wrapped_label(label)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("Inter", 12))
        label.setStyleSheet(f"color: {Theme.MUTED}; background: transparent; padding: 32px 24px;")
        self._hint_label = label
        self._layout.insertWidget(1, label)
        self._sync_viewport_width()

    def _insert_turn(self, turn: TurnBlock) -> None:
        if self._hint_label is not None:
            self._hint_label.hide()
        self._layout.addWidget(turn)
        self._sync_viewport_width()

    def _scroll_bottom(self) -> None:
        """Keep the latest turn in view.

        While the thread still fits the viewport the top stretch pins content to the
        bottom without scrolling; once it overflows the bar stays at 0 unless we
        explicitly jump to the maximum after layout settles.
        """
        def _do_scroll() -> None:
            from PyQt6 import sip
            if sip.isdeleted(self):
                return
            self._sync_viewport_width()
            self._root.updateGeometry()
            turn = self._current_turn
            if turn is not None:
                self.ensureWidgetVisible(turn, 0, 24)
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, _do_scroll)
        QTimer.singleShot(32, _do_scroll)

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._hint_label = None
        self._current_turn = None
        self._turns = []
        self._current_record = None
        self._last_meta = ""
        self._chunk_timer.stop()
        self._chunk_dirty = False
        self._live_answer = None
        self._live_answer_text = ""
        self._clarification_bar = None
        self._sync_viewport_width()
