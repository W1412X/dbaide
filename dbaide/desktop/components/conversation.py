"""Codex-style conversation: unified turn blocks with collapsible agent trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.progress_events import conversation_trace_step, trace_dedupe_keys
from dbaide.agent.trace_model import TraceModel
from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import configure_readonly_text_view, configure_wrapped_label
from dbaide.desktop.theme import Theme
from dbaide.rendering.markdown import render_markdown_safe


@dataclass(slots=True)
class TraceStep:
    kind: str
    message: str
    detail: str = ""


def classify_trace_message(message: str) -> str:
    text = message.strip()
    lowered = text.lower()
    if lowered.startswith("calling ") or "call_tool" in lowered:
        return "tool"
    if any(k in lowered for k in ("rows", "completed", "executed", "returned", "validated")):
        return "result"
    if any(k in lowered for k in ("discover", "generate", "route", "classif", "synthes", "risk", "retry")):
        return "decision"
    return "info"


_KIND_LABEL = {
    "decision": "Decision",
    "tool": "Action",
    "result": "Result",
    "info": "Info",
}

_KIND_COLOR = {
    "decision": Theme.YELLOW,
    "tool": Theme.BLUE,
    "result": Theme.GREEN,
    "info": Theme.MUTED,
}


class _Bubble(QFrame):
    def __init__(self, text: str, *, align_right: bool, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if align_right:
            layout.addStretch(1)
        label = QLabel(text)
        # User text is shown verbatim as PLAIN text — no markup is interpreted, so it
        # is XSS-safe without HTML-escaping (escaping here would surface entities like
        # &#x27; literally, since the label is not a rich-text view).
        label.setTextFormat(Qt.TextFormat.PlainText)
        # A chat bubble hugs its content up to a cap, then wraps. configure_wrapped_label
        # gives an *Ignored* horizontal policy, which would lose all width to the
        # leading stretch and collapse the bubble to nothing — so set the policy
        # explicitly here (Preferred + capped max width).
        label.setWordWrap(True)
        label.setMaximumWidth(560)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
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
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        if not align_right:
            layout.addStretch(1)


class CollapsibleTracePanel(QFrame):
    """Collapsible agent trace — decisions, tool calls, results."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("tracePanel")
        self.setStyleSheet(
            f"""
            QFrame#tracePanel {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 10px;
            }}
            """
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._toggle = QPushButton("  Agent trace")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setFlat(True)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.setFont(QFont("Inter", 11, QFont.Weight.DemiBold))
        self._toggle.toggled.connect(self._on_toggle)
        outer.addWidget(self._toggle)

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 0, 12, 12)
        body_layout.setSpacing(6)
        self._steps_layout = QVBoxLayout()
        self._steps_layout.setSpacing(8)
        body_layout.addLayout(self._steps_layout)
        outer.addWidget(self._body)

        self._steps: list[TraceStep] = []
        self._running = True
        self._model = TraceModel()
        self._refresh_header()

    def append(self, message: str, *, kind: str = "", detail: str = "") -> None:
        text = message.strip()
        if not text:
            return
        if self._steps and self._steps[-1].message == text and self._steps[-1].detail == detail:
            return
        step_kind = kind or classify_trace_message(text)
        self._steps.append(TraceStep(kind=step_kind, message=text, detail=detail))
        self._render_step(self._steps[-1])
        self._refresh_header()

    def append_from_event(self, event: dict[str, Any]) -> None:
        self._model.ingest(event)
        step = conversation_trace_step(event)
        if step is None:
            self._refresh_header()
            return
        message, kind, detail = step
        self.append(message, kind=kind, detail=detail)

    def extend_from_events(self, events: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for existing in self._steps:
            seen |= set(trace_dedupe_keys({"title": existing.message, "detail": existing.detail}))
        for event in events:
            self._model.ingest(event)
            step = conversation_trace_step(event)
            if step is None:
                continue
            message, kind, detail = step
            keys = set(trace_dedupe_keys(event)) | set(
                trace_dedupe_keys({"title": message, "detail": detail})
            )
            if keys & seen:
                continue
            seen |= keys
            self.append(message, kind=kind, detail=detail)

    def finish(self, *, ok: bool = True) -> None:
        self._running = False
        self._refresh_header(ok=ok)
        self.set_collapsed(True)

    def set_collapsed(self, collapsed: bool) -> None:
        self._toggle.setChecked(not collapsed)
        self._body.setVisible(not collapsed)

    def _on_toggle(self, expanded: bool) -> None:
        self._body.setVisible(expanded)

    def _refresh_header(self, *, ok: bool = True) -> None:
        chevron = "▾" if self._toggle.isChecked() else "▸"
        count = len(self._steps)
        if self._running:
            color = Theme.BLUE
            # Show what the agent is doing right now, plus how many sub-agents.
            phase = self._model.current_phase
            agents = self._model.active_agents
            bits = [f"{count} steps"]
            if phase:
                bits.append(phase)
            if agents:
                bits.append(f"{len(agents)} agent{'s' if len(agents) != 1 else ''}")
            label = "Agent trace · " + " · ".join(bits) + " …"
        else:
            color = Theme.GREEN if ok else Theme.RED
            status = "done" if ok else "failed"
            label = f"Agent trace · {count} steps · {status}"
        self._toggle.setText(f"{chevron}  {label}")
        self._toggle.setStyleSheet(
            f"""
            QPushButton {{
                color: {color};
                background: transparent;
                border: none;
                text-align: left;
                padding: 10px 12px;
            }}
            QPushButton:hover {{ color: {Theme.TEXT}; }}
            """
        )

    def _render_step(self, step: TraceStep) -> None:
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        head = QHBoxLayout()
        badge = QLabel(_KIND_LABEL.get(step.kind, "Info"))
        badge.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        badge.setStyleSheet(
            f"color: {_KIND_COLOR.get(step.kind, Theme.MUTED)};"
            f"background: {Theme.PANEL_2}; border-radius: 4px; padding: 2px 6px;"
        )
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        msg = QLabel(step.message)
        configure_wrapped_label(msg)
        msg.setFont(QFont("Inter", 11))
        msg.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        head.addWidget(msg, 1)
        layout.addLayout(head)
        if step.detail:
            detail = QLabel(step.detail[:400] + ("…" if len(step.detail) > 400 else ""))
            configure_wrapped_label(detail)
            detail.setFont(QFont("Menlo", 10))
            detail.setStyleSheet(
                f"color: {Theme.MUTED}; background: {Theme.CODE_BG};"
                f"border-radius: 6px; padding: 6px 8px; margin-left: 4px;"
            )
            layout.addWidget(detail)
        self._steps_layout.addWidget(row)


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
    """Option chips for ask_user clarification."""

    def __init__(self, options: list[str], parent=None) -> None:
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        hint = QLabel("Choose an option or type a reply below:")
        hint.setFont(QFont("Inter", 11))
        hint.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
        layout.addWidget(hint)
        layout.addStretch(1)
        self._buttons: list[QPushButton] = []
        for option in options:
            btn = compact_button(option, width=min(160, max(72, len(option) * 9)))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)
            self._buttons.append(btn)

    def connect_option(self, callback) -> None:
        for btn in self._buttons:
            label = btn.text()
            btn.clicked.connect(lambda _checked=False, value=label: callback(value))


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

        self.trace = CollapsibleTracePanel()
        self._layout.addWidget(self.trace)

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
        bubble_row = QWidget()
        bubble_row.setStyleSheet("background: transparent;")
        row = QHBoxLayout(bubble_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(_Bubble(text, align_right=True))
        self._header_layout.addWidget(bubble_row)

    def append_content(self, widget: QWidget) -> None:
        self._content_host.show()
        self._content.addWidget(widget)


class ConversationView(QScrollArea):
    _H_MARGIN = 20

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

    def begin_turn(self, user_text: str, *, meta: str = "") -> None:
        turn = TurnBlock()
        if user_text.strip():
            turn.set_user(user_text, meta=meta)
        self._insert_turn(turn)
        self._current_turn = turn
        self._current_record = {"question": user_text, "events": [], "answer": ""}
        self._turns.append(self._current_record)
        turn.trace.append("Starting agent…", kind="info")
        self._scroll_bottom()

    def append_trace(self, message: str, *, kind: str = "", detail: str = "") -> None:
        if self._current_turn is None:
            self.begin_turn("")
        assert self._current_turn is not None
        self._current_turn.trace.append(message, kind=kind, detail=detail)
        self._scroll_bottom()

    def append_trace_event(self, event: dict[str, Any]) -> None:
        if self._current_turn is None:
            self.begin_turn("")
        assert self._current_turn is not None
        self._current_turn.trace.append_from_event(event)
        if self._current_record is not None:
            self._current_record["events"].append(event)
        self._scroll_bottom()

    def append_clarification(self, *, question: str, options: list[str]) -> _ClarificationBar | None:
        if self._current_turn is None:
            self.begin_turn("")
        turn = self._current_turn
        assert turn is not None
        turn.trace.append("Waiting for your reply…", kind="info")
        turn.trace.finish(ok=True)
        turn.trace.set_collapsed(True)
        body = f"**Clarification needed**\n\n{question}"
        if options:
            body += "\n\n" + "\n".join(f"- {item}" for item in options)
        turn.append_content(_MarkdownBlock(body, title="DBAide"))
        bar = _ClarificationBar(options) if options else None
        if bar is not None:
            turn.append_content(bar)
        self._scroll_bottom()
        return bar

    def append_clarification_reply(self, text: str) -> None:
        if self._current_turn is None:
            return
        bubble_row = QWidget()
        bubble_row.setStyleSheet("background: transparent;")
        row = QHBoxLayout(bubble_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(_Bubble(text, align_right=True))
        self._current_turn._header.show()
        self._current_turn._header_layout.addWidget(bubble_row)
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
        if trace_events:
            turn.trace.extend_from_events(trace_events)
        if self._current_record is not None:
            if trace_events:  # the persisted trace is the authoritative, complete one
                self._current_record["events"] = list(trace_events)
            self._current_record["answer"] = answer
        turn.trace.finish(ok=ok)

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
            self._current_turn.trace.finish(ok=False)
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
