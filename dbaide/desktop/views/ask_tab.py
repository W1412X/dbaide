from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.conversation import ConversationView, classify_trace_message
from dbaide.desktop.components.composer_options import POLICY_LABELS
from dbaide.desktop.components.empty_state import EmptyState
from dbaide.desktop.components.menu import MenuButton


class AskTab(QWidget):
    open_sql = pyqtSignal(str)
    empty_action = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QWidget()
        self.stack_layout = QVBoxLayout(self.stack)
        self.empty = EmptyState(
            "Connect your first database",
            "Open Settings to add a connection and configure the model.",
            [],
        )
        self._empty_btn = compact_button("Open Settings", primary=True, width=128)
        self._empty_btn.clicked.connect(lambda: self.empty_action.emit("settings"))
        self.stack_layout.addWidget(self.empty)
        empty_actions = QWidget()
        empty_row = QHBoxLayout(empty_actions)
        empty_row.setContentsMargins(0, 0, 0, 0)
        empty_row.addStretch(1)
        empty_row.addWidget(self._empty_btn)
        empty_row.addStretch(1)
        self.stack_layout.addWidget(empty_actions)

        self.conversation = ConversationView()
        self.stack_layout.addWidget(self.conversation, 1)
        self.conversation.hide()

        layout.addWidget(self.stack, 1)
        self._turn_open = False
        self._hint_shown = False

    def set_has_connection(self, has_connection: bool) -> None:
        self.empty.setVisible(not has_connection)
        self._empty_btn.parentWidget().setVisible(not has_connection)
        self.conversation.setVisible(has_connection)
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
        self.conversation.append_trace(message, kind=classify_trace_message(message))

    def finish_turn_error(self, message: str) -> None:
        self.conversation.finish_turn_error(message)
        self._turn_open = False

    def append_user(self, question: str, *, connection: str, database: str, policy: str) -> None:
        self.begin_turn(question, connection=connection, database=database, policy=policy)

    def append_result(self, result: dict[str, Any]) -> None:
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
        from collections.abc import Callable

        menu_actions: list[tuple[str, Callable[[], None]]] = [
            ("Copy SQL", lambda: QApplication.clipboard().setText(sql)),
            ("Open in SQL Tab", lambda: self.open_sql.emit(sql)),
        ]
        if cli_command:
            menu_actions.append(("Copy CLI", lambda: QApplication.clipboard().setText(str(cli_command))))
        actions = MenuButton("Actions ▾", max_width=120)
        for label, callback in menu_actions:
            actions.add_action(label, callback)
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(actions)
        row.addStretch(1)
        return bar

    def append_note(self, title: str, body: str) -> None:
        self.conversation.begin_turn("")
        self.conversation.complete_turn(answer=f"**{title}**\n\n{body}", ok=True)

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
