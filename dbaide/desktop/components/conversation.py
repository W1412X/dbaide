"""Codex-style conversation: unified turn blocks with collapsible agent trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.inputs import configure_wrapped_label
from dbaide.desktop.theme import Theme
from dbaide.rendering.markdown import render_markdown_safe
from dbaide.rendering.sanitize import escape_user_text


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
        label = QLabel(escape_user_text(text))
        configure_wrapped_label(label, max_width=760)
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
        self._toggle.setFont(QFont("Inter", 11, QFont.Weight.DemiBold))
        self._toggle.toggled.connect(self._on_toggle)
        outer.addWidget(self._toggle)

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 0, 12, 12)
        body_layout.setSpacing(6)
        self._steps_layout = QVBoxLayout()
        self._steps_layout.setSpacing(8)
        body_layout.addLayout(self._steps_layout)
        outer.addWidget(self._body)

        self._steps: list[TraceStep] = []
        self._running = True
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

    def extend_from_events(self, events: list[dict[str, Any]]) -> None:
        seen = {s.message for s in self._steps}
        for event in events:
            stage = str(event.get("stage") or "")
            title = str(event.get("title") or "")
            summary = str(event.get("summary") or "")
            output = str(event.get("output_preview") or "")
            if stage in {"agent_progress"} and summary:
                if summary in seen:
                    continue
                self.append(summary, kind=classify_trace_message(summary))
                seen.add(summary)
                continue
            if stage in {"workflow_started", "planning"}:
                continue
            if stage.startswith("execute") or stage == "execution_completed":
                kind = "result"
            elif stage in {"sql_generated", "sql_validation"}:
                kind = "result" if stage == "sql_validation" else "decision"
            elif str(event.get("actor") or "") == "tool":
                kind = "tool"
            else:
                kind = classify_trace_message(title or summary)
            msg = title or summary or stage
            if not msg or msg in seen:
                continue
            detail = output if output and output not in msg else ""
            if summary and summary != msg and not detail:
                detail = summary
            self.append(msg, kind=kind, detail=detail)
            seen.add(msg)

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
            status = "running…"
            color = Theme.BLUE
        else:
            status = "done" if ok else "failed"
            color = Theme.GREEN if ok else Theme.RED
        self._toggle.setText(f"{chevron}  Agent trace · {count} steps · {status}")
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


class _MarkdownBlock(QWidget):
    def __init__(self, markdown: str, *, title: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if title:
            t = QLabel(title)
            t.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            t.setStyleSheet(f"color: {Theme.MUTED};")
            layout.addWidget(t)
        body = QLabel()
        configure_wrapped_label(body)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        body.setFont(QFont("Inter", 13))
        html = render_markdown_safe(markdown or "")
        body.setText(
            f"<style>p{{margin:4px 0;}} pre,code{{background:{Theme.CODE_BG};"
            f"border-radius:8px;padding:8px;font-family:Menlo,monospace;font-size:11px;}}"
            f"a{{color:{Theme.BLUE};}}</style>{html}"
        )
        layout.addWidget(body)


class TurnBlock(QFrame):
    """One complete Q&A turn in a single scroll block."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("turnBlock")
        self.setStyleSheet("QFrame#turnBlock { background: transparent; border: none; }")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 12, 0, 20)
        self._layout.setSpacing(10)
        self.trace = CollapsibleTracePanel()
        self._layout.addWidget(self.trace)
        self._content = QVBoxLayout()
        self._content.setSpacing(10)
        self._layout.addLayout(self._content)

    def set_user(self, text: str, *, meta: str = "") -> None:
        insert_at = 0
        if meta:
            meta_label = QLabel(meta)
            meta_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            meta_label.setFont(QFont("Inter", 10))
            meta_label.setStyleSheet(f"color: {Theme.MUTED};")
            self._layout.insertWidget(insert_at, meta_label)
            insert_at += 1
        bubble_row = QWidget()
        bubble_row.setStyleSheet("background: transparent;")
        row = QHBoxLayout(bubble_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(_Bubble(text, align_right=True))
        self._layout.insertWidget(insert_at, bubble_row)

    def append_content(self, widget: QWidget) -> None:
        self._content.addWidget(widget)


class ConversationView(QScrollArea):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea {{ border: none; background: {Theme.BG}; }}")

        self._root = QWidget()
        self._root.setStyleSheet(f"background: {Theme.BG};")
        self._column = QVBoxLayout(self._root)
        self._column.setContentsMargins(0, 0, 0, 0)

        self._stream = QWidget()
        self._stream.setMaximumWidth(1180)
        self._stream.setMinimumWidth(640)
        self._stream.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._stream_layout = QVBoxLayout(self._stream)
        self._stream_layout.setContentsMargins(16, 8, 16, 32)
        self._stream_layout.setSpacing(0)
        self._stream_layout.addStretch(1)

        center = QHBoxLayout()
        center.setContentsMargins(12, 0, 12, 0)
        center.addWidget(self._stream, 1)
        wrap = QWidget()
        wrap.setLayout(center)
        self._column.addWidget(wrap)
        self.setWidget(self._root)
        self._current_turn: TurnBlock | None = None

    def begin_turn(self, user_text: str, *, meta: str = "") -> None:
        turn = TurnBlock()
        if user_text.strip():
            turn.set_user(user_text, meta=meta)
        self._insert_turn(turn)
        self._current_turn = turn
        turn.trace.append("Starting agent…", kind="info")
        self._scroll_bottom()

    def append_trace(self, message: str, *, kind: str = "", detail: str = "") -> None:
        if self._current_turn is None:
            self.begin_turn("")
        assert self._current_turn is not None
        self._current_turn.trace.append(message, kind=kind, detail=detail)
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
        self._scroll_bottom()

    def finish_turn_error(self, message: str) -> None:
        if self._current_turn:
            self._current_turn.trace.finish(ok=False)
            self._current_turn.append_content(_MarkdownBlock(message, title="Error"))
            self._current_turn = None
        else:
            self.begin_turn("")
            self.complete_turn(answer=message, ok=False)

    def append_hint(self, text: str) -> None:
        label = QLabel(text)
        configure_wrapped_label(label)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("Inter", 12))
        label.setStyleSheet(f"color: {Theme.MUTED}; padding: 64px 24px;")
        self._stream_layout.insertWidget(0, label)

    def _insert_turn(self, turn: TurnBlock) -> None:
        idx = max(0, self._stream_layout.count() - 1)
        self._stream_layout.insertWidget(idx, turn)

    def _scroll_bottom(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def clear(self) -> None:
        while self._stream_layout.count() > 1:
            item = self._stream_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._current_turn = None
