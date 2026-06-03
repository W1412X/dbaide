from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.conversation import ConversationView
from dbaide.desktop.components.composer_options import POLICY_LABELS
from dbaide.desktop.components.empty_state import EmptyState


class AskTab(QWidget):
    open_sql = pyqtSignal(str)
    empty_action = pyqtSignal(str)
    clarification_choice = pyqtSignal(str)
    trace_requested = pyqtSignal(object)  # show a turn's trace in the right panel

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        # Balance the trailing stretch with a leading one so the empty-state column
        # is vertically centred in the tab (was top-aligned, clipping the title).
        empty_layout.addStretch(1)
        from dbaide.i18n import t
        self.empty = EmptyState(
            t("ask.empty_title"),
            t("ask.empty_subtitle"),
            [],
        )
        self._empty_btn = compact_button(t("ask.open_settings"), primary=True, width=128)
        self._empty_btn.clicked.connect(lambda: self.empty_action.emit("settings"))
        empty_layout.addWidget(self.empty)
        empty_actions = QWidget()
        empty_row = QHBoxLayout(empty_actions)
        empty_row.setContentsMargins(0, 0, 0, 0)
        empty_row.addStretch(1)
        empty_row.addWidget(self._empty_btn)
        empty_row.addStretch(1)
        empty_layout.addWidget(empty_actions)
        empty_layout.addStretch(1)

        self.conversation = ConversationView()
        self.conversation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.conversation.trace_requested.connect(self.trace_requested)
        self.stack.addWidget(empty_page)
        self.stack.addWidget(self.conversation)
        layout.addWidget(self.stack, 1)
        self._turn_open = False
        self._hint_shown = False

    def set_has_connection(self, has_connection: bool) -> None:
        self.stack.setCurrentIndex(1 if has_connection else 0)
        if has_connection and not self._hint_shown:
            self.conversation.append_hint("Ask about your schema or data in natural language.")
            self._hint_shown = True

    def begin_turn(self, question: str, *, connection: str, database: str, policy: str) -> None:
        policy_label = POLICY_LABELS.get(policy, policy)
        meta = " · ".join(x for x in (connection, database or "auto", policy_label) if x)
        self.conversation.begin_turn(question, meta=meta)
        self._turn_open = True

    def append_activity(self, message: str) -> None:
        if not self._turn_open:
            return
        self.conversation.append_trace(message)

    def append_activity_event(self, event: dict) -> None:
        if not self._turn_open:
            return
        self.conversation.append_trace_event(event)

    def finish_turn_error(self, message: str) -> None:
        self.conversation.finish_turn_error(message)
        self._turn_open = False

    def append_user(self, question: str, *, connection: str, database: str, policy: str) -> None:
        self.begin_turn(question, connection=connection, database=database, policy=policy)

    def append_clarification_reply(self, text: str) -> None:
        self.conversation.append_clarification_reply(text)

    def show_clarification(self, result: dict[str, Any]) -> None:
        """Pause the current turn and show ask_user prompt with optional chips."""
        question = str(result.get("pending_question") or result.get("answer_markdown") or "")
        options = [str(item) for item in (result.get("pending_options") or []) if str(item).strip()]
        self._turn_open = True
        bar = self.conversation.append_clarification(question=question, options=options)
        if bar is not None:
            bar.connect_option(self.clarification_choice.emit)

    def append_result(self, result: dict[str, Any]) -> None:
        if str(result.get("status") or "") == "wait_user":
            self.show_clarification(result)
            return
        status = str(result.get("status") or "completed")
        workflow_id = str(result.get("workflow_id") or "")
        ok = status not in ("failed", "cancelled")
        self._turn_open = False

        answer = result.get("answer_markdown") or result.get("answer_plaintext") or ""
        sql = result.get("selected_sql") or ""

        self.conversation.complete_turn(
            answer=answer,
            sql=sql,
            trace_events=result.get("trace") or [],
            warnings=result.get("warnings") or None,
            errors=result.get("errors") or None,
            workflow_id=workflow_id,
            ok=ok,
            actions_widget=self._build_actions(sql, result.get("cli_command")),
        )

    def _build_actions(self, sql: str, cli_command: str | None) -> QWidget | None:
        if not sql:
            return None
        from PyQt6.QtCore import QSize, QTimer
        from dbaide.desktop.components.base import ghost_action_button
        from dbaide.desktop.components.icons import svg_icon
        from dbaide.desktop.theme import Theme

        def _copy_btn(label: str, payload: str) -> QWidget:
            btn = ghost_action_button(
                label, icon=svg_icon("copy", color=Theme.MUTED, size=14), tooltip=label
            )

            def _do() -> None:
                QApplication.clipboard().setText(payload)
                btn.setText("Copied")
                btn.setIcon(svg_icon("check", color=Theme.GREEN, size=14))
                QTimer.singleShot(
                    1200,
                    lambda: (btn.setText(label), btn.setIcon(svg_icon("copy", color=Theme.MUTED, size=14))),
                )

            btn.clicked.connect(_do)
            return btn

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(2)
        row.addWidget(_copy_btn("Copy SQL", sql))
        open_btn = ghost_action_button(
            "Open in SQL", icon=svg_icon("external-link", color=Theme.MUTED, size=14),
            tooltip="Open this query in the SQL tab",
        )
        open_btn.clicked.connect(lambda: self.open_sql.emit(sql))
        row.addWidget(open_btn)
        if cli_command:
            row.addWidget(_copy_btn("Copy CLI", str(cli_command)))
        row.addStretch(1)
        return bar

    def append_note(self, title: str, body: str) -> None:
        self.conversation.begin_turn("")
        self.conversation.complete_turn(answer=f"**{title}**\n\n{body}", ok=True)

    def copy_text(self) -> str:
        """Full conversation export (all turns: question, trace, answer)."""
        return self.conversation.copy_text()

    def clear_conversation(self) -> None:
        self.conversation.clear()
        self._turn_open = False

    def load_session(self, turns: list[dict[str, Any]], *, connection: str = "") -> None:
        """Render a saved chat session's turns (question → trace → answer) into the
        conversation, replacing whatever is currently shown."""
        self.conversation.clear()
        self._hint_shown = True  # don't show the intro hint over a restored thread
        self.stack.setCurrentIndex(1)
        from dbaide.desktop.components.composer_options import POLICY_LABELS
        for turn in turns:
            meta = turn.get("meta") or {}
            database = str(meta.get("database") or "")
            policy = str(meta.get("policy") or "safe_auto")
            meta_line = " · ".join(
                x for x in (connection, database or "auto", POLICY_LABELS.get(policy, policy)) if x
            )
            # placeholder=False: restored turns show only their saved trace, not the
            # live "Starting agent…" line.
            self.conversation.begin_turn(
                str(turn.get("question") or ""), meta=meta_line, placeholder=False,
            )
            sql = str(turn.get("selected_sql") or "")
            status = str(turn.get("status") or "completed")
            self.conversation.complete_turn(
                answer=str(turn.get("answer_markdown") or ""),
                sql=sql,
                trace_events=turn.get("trace") or [],
                ok=status not in ("failed", "cancelled"),
                actions_widget=self._build_actions(sql, None),
            )
        self._turn_open = False

    def append_search_hits(self, query: str, hits: list[dict[str, Any]]) -> None:
        if not hits:
            body = f"No matches for `{query}`. Try building assets or asking in natural language."
        else:
            lines = [f"Found {len(hits)} matches for `{query}`:", ""]
            for hit in hits:
                lines.append(f"- **{hit.get('path')}** ({hit.get('kind')}, score {hit.get('score', 0):.1f})")
                if hit.get("summary"):
                    lines.append(f"  {hit['summary'][:160]}")
            body = "\n".join(lines)
        self.conversation.begin_turn(query)
        self.conversation.complete_turn(answer=body, ok=True)
