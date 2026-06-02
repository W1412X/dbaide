"""Codex-style conversation: question bubbles + answers, with a lightweight
"thinking" indicator per turn. The detailed agent trace lives in the right panel,
not inline — clicking a turn's indicator reveals it there."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.progress_events import conversation_trace_step, phase_for
from dbaide.desktop.components.base import AgentButton, compact_button
from dbaide.desktop.components.inputs import configure_readonly_text_view, configure_wrapped_label
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
from dbaide.desktop.theme import Theme
from dbaide.rendering.markdown import render_markdown_safe


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
        label.setFont(QFont("Inter", 13))
        label.setStyleSheet(
            f"""
            background: {Theme.PANEL_3};
            color: {Theme.TEXT};
            border: 1px solid {Theme.BORDER};
            border-radius: 18px;
            padding: 10px 16px;
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


class _ThinkingIndicator(QPushButton):
    """Per-turn status chip. While the agent runs it shows a spinner + the current
    phase ("Thinking…", then phase labels); when done it collapses to a muted
    "View agent trace · N steps" link. Clicking it reveals the full trace in the
    right panel (it carries no trace detail itself). Emits ``opened`` with the
    turn's events (or None while still running → just reveal the live trace)."""

    opened = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFont(QFont("Inter", 11, QFont.Weight.DemiBold))
        self._running = False
        self._waiting = False
        self._phase = "Thinking…"
        self._events: list[dict[str, Any]] = []
        self._ok = True
        self._step_count = 0
        self._busy = BusyAnimator(self._tick)
        self.clicked.connect(self._on_click)
        self._sync()

    # ── state transitions ──────────────────────────────────────────────────--

    def start(self, phase: str = "Thinking…") -> None:
        self._running, self._waiting = True, False
        if phase:
            self._phase = phase
        if not self._busy.active:
            self._busy.start()
        self._sync()

    def set_phase(self, phase: str) -> None:
        if not phase:
            return
        # A live event arrived — (re)enter the running state and show the phase.
        self._running, self._waiting = True, False
        self._phase = phase if len(phase) <= 60 else phase[:59] + "…"
        if not self._busy.active:
            self._busy.start()
        self._sync()

    def set_waiting(self, text: str = "Waiting for your reply…") -> None:
        self._running, self._waiting = False, True
        self._busy.stop()
        self._phase = text
        self._sync()

    def set_done(self, *, ok: bool, step_count: int, events: list[dict[str, Any]]) -> None:
        self._running, self._waiting = False, False
        self._busy.stop()
        self._ok = ok
        self._step_count = max(0, int(step_count))
        self._events = list(events or [])
        self._sync()

    # ── internals ──────────────────────────────────────────────────────────--

    def _on_click(self) -> None:
        # While running the live trace is already in the right panel — just reveal
        # it (None). When done, hand over this turn's events to show.
        self.opened.emit(None if self._running else self._events)

    def _tick(self) -> None:
        self.setIcon(spinner_icon(self._busy.angle, color=Theme.BLUE))

    def _sync(self) -> None:
        if self._running:
            color = Theme.BLUE
            phase = self._phase if self._phase.endswith("…") else f"{self._phase}…"
            self.setIcon(spinner_icon(self._busy.angle, color=Theme.BLUE))
            self.setText(f"  {phase}")
            self.show()
        elif self._waiting:
            color = Theme.YELLOW
            self.setIcon(QIcon())
            self.setText(self._phase)
            self.show()
        else:
            self.setIcon(QIcon())
            if self._step_count <= 0:
                self.hide()  # nothing to reveal — don't show a hollow chip
                return
            color = Theme.MUTED if self._ok else Theme.RED
            # No step count here — the right panel is the source of truth for that
            # (and its filtered count differs from the raw event count).
            self.setText(("View agent trace ›" if self._ok else "View agent trace · failed ›"))
            self.show()
        self.setStyleSheet(
            f"""
            QPushButton {{
                color: {color};
                background: transparent;
                border: none;
                text-align: left;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ color: {Theme.TEXT}; }}
            """
        )


class _MarkdownBlock(QFrame):
    def __init__(self, markdown: str, *, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("answerBlock")
        self.setStyleSheet(
            f"""
            QFrame#answerBlock {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            t.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
            layout.addWidget(t)
        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setFont(QFont("Inter", 13))
        configure_readonly_text_view(self._body)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; color: {Theme.TEXT}; padding: 0; }}"
        )
        html = render_markdown_safe(markdown or "")
        self._body.setHtml(
            f"<style>body{{margin:0;color:{Theme.TEXT};font-family:Inter,sans-serif;font-size:13px;}}"
            f"p{{margin:4px 0;}} pre,code{{background:{Theme.CODE_BG};"
            f"border-radius:8px;padding:8px;font-family:Menlo,monospace;font-size:11px;white-space:pre-wrap;}}"
            f"table.md-table{{border-collapse:collapse;width:100%;margin:8px 0;}}"
            f"table.md-table th,table.md-table td{{border:1px solid {Theme.BORDER_SOFT};padding:6px 10px;text-align:left;}}"
            f"table.md-table th{{background:{Theme.PANEL_2};font-weight:600;}}"
            f"table.md-table tr:nth-child(even) td{{background:{Theme.PANEL};}}"
            f"a{{color:{Theme.BLUE};}}</style>{html}"
        )
        layout.addWidget(self._body)
        self._body.document().documentLayout().documentSizeChanged.connect(self._sync_body_height)
        self._sync_body_height()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_body_height()

    def _sync_body_height(self, *_args) -> None:
        doc = self._body.document()
        width = max(self._body.viewport().width(), self.width() - 32, 320)
        doc.setTextWidth(width)
        height = int(doc.documentLayout().documentSize().height()) + 8
        self._body.setFixedHeight(max(height, 24))


class _ClarificationBar(QFrame):
    """Reply controls for a clarification: full-text option chips (they wrap, never
    truncate) plus an inline free-text input + Send. When there are several
    questions a chip only answers one, so it fills the input (the user completes the
    rest and sends) instead of submitting immediately — which would discard the
    other answers."""

    submitted = pyqtSignal(str)

    def __init__(self, options: list[str], *, allow_direct_submit: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("clarificationBar")
        self.setStyleSheet(
            f"""
            QFrame#clarificationBar {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 10px;
            }}
            """
        )
        self._direct = allow_direct_submit
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)

        if options:
            from dbaide.desktop.components.flow_layout import FlowLayout
            chips_host = QWidget()
            chips_host.setStyleSheet("background: transparent;")
            chips = FlowLayout(chips_host, spacing=6)
            for option in options:
                btn = AgentButton(option)            # sizes to its full text — no truncation
                btn.setFixedHeight(30)
                btn.setMaximumWidth(360)             # very long → clips with a tooltip (full text)
                btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                btn.setToolTip(option)
                btn.clicked.connect(lambda _c=False, v=option: self._on_chip(v))
                chips.addWidget(btn)
            outer.addWidget(chips_host)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "Type your answer…" if allow_direct_submit else "Type your answers (one line covers all the questions)…"
        )
        self._input.setFixedHeight(30)
        self._input.returnPressed.connect(self._on_send)
        row.addWidget(self._input, 1)
        self._send = compact_button("Send", primary=True, width=72)
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

    def connect_option(self, callback) -> None:
        """Back-compat shim: route the unified submission to the callback."""
        self.submitted.connect(callback)


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
        # link). The detailed trace lives in the right panel — not inline.
        self.status = _ThinkingIndicator()
        self._layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)

        self._content_host = QWidget()
        self._content_host.setStyleSheet("background: transparent;")
        self._content_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._content = QVBoxLayout(self._content_host)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(10)
        self._content_host.hide()
        self._layout.addWidget(self._content_host)

    def set_user(self, text: str, *, meta: str = "") -> None:
        self._header.show()
        if meta:
            meta_label = QLabel(meta)
            meta_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            meta_label.setFont(QFont("Inter", 10))
            meta_label.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
            self._header_layout.addWidget(meta_label)
        self._header_layout.addWidget(_Bubble(text, align_right=True))

    def append_content(self, widget: QWidget) -> None:
        self._content_host.show()
        self._content.addWidget(widget)


class ConversationView(QScrollArea):
    _H_MARGIN = 20

    # Emitted when a turn's status chip is clicked: the turn's trace events to show
    # in the right panel, or None (still running → just reveal the live trace).
    trace_requested = pyqtSignal(object)

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
        # Retained per-turn records (question, trace events, answer) for "copy the
        # whole conversation's trace".
        self._turns: list[dict[str, Any]] = []
        self._current_record: dict[str, Any] | None = None
        self._clarification_bar: _ClarificationBar | None = None

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
        content_w = max(200, viewport_w - self._H_MARGIN * 2)
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setMinimumWidth(content_w)

    def begin_turn(self, user_text: str, *, meta: str = "", placeholder: bool = True) -> None:
        turn = TurnBlock()
        if user_text.strip():
            turn.set_user(user_text, meta=meta)
        self._insert_turn(turn)
        self._current_turn = turn
        self._current_record = {"question": user_text, "events": [], "answer": ""}
        self._turns.append(self._current_record)
        turn.status.opened.connect(self.trace_requested)
        # placeholder=True: a live run → spin immediately. placeholder=False: a
        # restored turn → stays idle until complete_turn sets its "view trace" link.
        if placeholder:
            turn.status.start("Thinking…")
        self._scroll_bottom()

    def append_trace(self, message: str, *, kind: str = "", detail: str = "") -> None:
        if self._current_turn is None:
            self.begin_turn("")
        assert self._current_turn is not None
        if message.strip():
            self._current_turn.status.set_phase(message.strip())
        self._scroll_bottom()

    def append_trace_event(self, event: dict[str, Any]) -> None:
        if self._current_turn is None:
            self.begin_turn("")
        assert self._current_turn is not None
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
        self._scroll_bottom()

    def append_clarification(self, *, question: str, options: list[str]) -> _ClarificationBar | None:
        if self._current_turn is None:
            self.begin_turn("")
        turn = self._current_turn
        assert turn is not None
        turn.status.set_waiting()
        # Options are presented as the chip bar below — don't also bullet them in the
        # body (the question text already conveys the choices).
        turn.append_content(_MarkdownBlock(f"**Clarification needed**\n\n{question}", title="DBAide"))
        # If the prompt poses several numbered questions, a single chip only answers
        # one — so chips fill the input (assemble all answers) rather than submit.
        multi = sum(1 for i in range(1, 10) if f"**{i}." in question) >= 2
        # Always offer the bar (its input box handles open questions with no options).
        bar = _ClarificationBar(options, allow_direct_submit=not multi)
        turn.append_content(bar)
        self._clarification_bar = bar
        self._scroll_bottom()
        return bar

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
        sql: str = "",
        trace_events: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        workflow_id: str = "",
        ok: bool = True,
        actions_widget: QWidget | None = None,
    ) -> None:
        if self._current_turn is None:
            self.begin_turn("")
        turn = self._current_turn
        assert turn is not None
        # The persisted trace is the authoritative one; fall back to whatever streamed
        # in live. These events feed the right panel when the chip is clicked.
        events = list(trace_events) if trace_events else list(
            (self._current_record or {}).get("events") or []
        )
        if self._current_record is not None:
            if trace_events:
                self._current_record["events"] = list(trace_events)
            self._current_record["answer"] = answer
        turn.status.set_done(ok=ok, step_count=len(events), events=events)

        subtitle = f"DBAide · {workflow_id}" if workflow_id else "DBAide"
        if answer.strip():
            turn.append_content(_MarkdownBlock(answer, title=subtitle))
        if sql.strip() and "```sql" not in answer:
            turn.append_content(_MarkdownBlock(f"```sql\n{sql}\n```", title="SQL"))
        if actions_widget is not None:
            turn.append_content(actions_widget)
        notes: list[str] = []
        if warnings:
            notes.append("**Warnings**\n" + "\n".join(f"- {w}" for w in warnings))
        if errors:
            lines = []
            for err in errors:
                if isinstance(err, dict):
                    lines.append(f"- [{err.get('stage', '')}] {err.get('message', '')}")
                else:
                    lines.append(f"- {err}")
            notes.append("**Notes**\n" + "\n".join(lines))
        if notes:
            turn.append_content(_MarkdownBlock("\n\n".join(notes)))

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

    def finish_turn_error(self, message: str) -> None:
        if self._current_turn:
            events = list((self._current_record or {}).get("events") or [])
            self._current_turn.status.set_done(ok=False, step_count=len(events), events=events)
            self._current_turn.append_content(_MarkdownBlock(message, title="Error"))
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
        def _do_scroll() -> None:
            self._sync_viewport_width()
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, _do_scroll)

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
        self._sync_viewport_width()
