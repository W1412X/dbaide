from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.conversation import ConversationView
from dbaide.desktop.components.composer_options import POLICY_LABELS
from dbaide.desktop.components.empty_state import EmptyState


class AskTab(QWidget):
    """Multi-session conversation surface.

    Each session (会话) gets its own ConversationView, kept in a QStackedWidget so a
    background run keeps updating its own view live even while another session is on
    screen. Methods that mutate a conversation take an explicit ``key`` (the session
    slot) so a run's progress/result always lands in the right place, regardless of
    which session is currently visible. ``key`` is the server session_id once known,
    or a temporary client key (``new:N``) for a brand-new, not-yet-saved chat.
    """

    open_sql = pyqtSignal(str)
    empty_action = pyqtSignal(str)
    clarification_choice = pyqtSignal(str, str)   # (slot key, reply)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()

        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.addStretch(1)
        from dbaide.i18n import t
        self.empty = EmptyState(t("ask.empty_title"), t("ask.empty_subtitle"), [])
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
        self.stack.addWidget(empty_page)  # index 0 — always present

        layout.addWidget(self.stack, 1)
        self._views: dict[str, ConversationView] = {}
        self._active: str = ""
        self._has_conn = False
        self._hint_shown = False
        self._stream_answers = True

    def set_stream_answers(self, enabled: bool) -> None:
        """Toggle the progressive answer reveal for all conversation slots (live)."""
        self._stream_answers = bool(enabled)
        for view in self._views.values():
            view.set_stream_answers(self._stream_answers)

    # ── slot lifecycle ────────────────────────────────────────────────────────

    def ensure_slot(self, key: str) -> ConversationView:
        """Return the ConversationView for ``key``, creating + wiring it if needed."""
        view = self._views.get(key)
        if view is None:
            view = ConversationView()
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            view.set_stream_answers(self._stream_answers)
            self._views[key] = view
            self.stack.addWidget(view)
            if self._has_conn and not self._hint_shown:
                view.append_hint("Ask about your schema or data in natural language.")
        return view

    def has_slot(self, key: str) -> bool:
        return key in self._views

    def set_active(self, key: str) -> None:
        """Show ``key``'s conversation (creating its view), or the empty page for
        ``""`` / when there's no connection."""
        self._active = key
        if key and self._has_conn:
            self.stack.setCurrentWidget(self.ensure_slot(key))
        else:
            self.stack.setCurrentIndex(0)

    def active_key(self) -> str:
        return self._active

    def remap(self, old_key: str, new_key: str) -> None:
        """Re-key a slot (a new chat's temporary key → the server session_id)."""
        if old_key == new_key or old_key not in self._views:
            return
        view = self._views.pop(old_key)
        # Drop a stale view already sitting under new_key (shouldn't normally happen).
        existing = self._views.pop(new_key, None)
        if existing is not None and existing is not view:
            self.stack.removeWidget(existing)
            existing.deleteLater()
        self._views[new_key] = view
        if self._active == old_key:
            self._active = new_key

    def discard_slot(self, key: str) -> None:
        view = self._views.pop(key, None)
        if view is not None:
            self.stack.removeWidget(view)
            view.deleteLater()
        if self._active == key:
            self._active = ""
            self.stack.setCurrentIndex(0)

    def view(self, key: str) -> ConversationView | None:
        return self._views.get(key)

    def reset_all(self) -> None:
        """Drop every slot (e.g. when the connection changes)."""
        for key in list(self._views.keys()):
            view = self._views.pop(key)
            self.stack.removeWidget(view)
            view.deleteLater()
        self._active = ""
        self._hint_shown = False
        self.stack.setCurrentIndex(0)

    def turn_open(self, key: str) -> bool:
        view = self._views.get(key)
        return bool(view and view.has_open_turn())

    # ── connection / empty-state ──────────────────────────────────────────────

    def set_has_connection(self, has_connection: bool) -> None:
        self._has_conn = has_connection
        self.set_active(self._active)

    # ── keyed conversation operations ─────────────────────────────────────────

    def begin_turn(self, key: str, question: str, *, connection: str, database: str, policy: str,
                   attachments: list[dict] | None = None) -> None:
        policy_label = POLICY_LABELS.get(policy, policy)
        meta = " · ".join(x for x in (connection, database or "auto", policy_label) if x)
        self._hint_shown = True
        self.ensure_slot(key).begin_turn(question, meta=meta, attachments=attachments)

    def append_user(self, key: str, question: str, *, connection: str, database: str, policy: str,
                    attachments: list[dict] | None = None) -> None:
        self.begin_turn(key, question, connection=connection, database=database, policy=policy,
                        attachments=attachments)

    def append_activity(self, key: str, message: str) -> None:
        view = self._views.get(key)
        if view is not None and view.has_open_turn():
            view.append_trace(message)

    def append_activity_event(self, key: str, event: dict) -> None:
        view = self._views.get(key)
        if view is not None and view.has_open_turn():
            view.append_trace_event(event)

    def finish_turn_error(self, key: str, message: str) -> None:
        view = self.ensure_slot(key)
        view.finish_turn_error(message)

    def append_clarification_reply(self, key: str, text: str) -> None:
        view = self._views.get(key)
        if view is not None:
            view.append_clarification_reply(text)

    def show_clarification(self, key: str, result: dict[str, Any]) -> None:
        question = str(result.get("pending_question") or result.get("answer_markdown") or "")
        options = [str(item) for item in (result.get("pending_options") or []) if str(item).strip()]
        questions = [q for q in (result.get("pending_questions") or []) if isinstance(q, dict)]
        view = self.ensure_slot(key)
        bar = view.append_clarification(question=question, options=options, questions=questions)
        if bar is not None:
            bar.connect_option(lambda reply, k=key: self.clarification_choice.emit(k, reply))

    def append_result(self, key: str, result: dict[str, Any]) -> None:
        if str(result.get("status") or "") == "wait_user":
            self.show_clarification(key, result)
            return
        status = str(result.get("status") or "completed")
        workflow_id = str(result.get("workflow_id") or "")
        ok = status not in ("failed", "cancelled")
        answer = result.get("answer_markdown") or result.get("answer_plaintext") or ""
        sql = result.get("selected_sql") or ""
        self.ensure_slot(key).complete_turn(
            answer=answer,
            sql=sql,
            trace_events=result.get("trace") or [],
            warnings=result.get("warnings") or None,
            errors=result.get("errors") or None,
            workflow_id=workflow_id,
            ok=ok,
            actions_widget=self._build_actions(sql, result.get("cli_command")),
        )

    def append_note(self, key: str, title: str, body: str) -> None:
        view = self.ensure_slot(key)
        view.begin_turn("")
        view.complete_turn(answer=f"**{title}**\n\n{body}", ok=True)

    def append_search_hits(self, key: str, query: str, hits: list[dict[str, Any]]) -> None:
        if not hits:
            body = f"No matches for `{query}`. Try building assets or asking in natural language."
        else:
            lines = [f"Found {len(hits)} matches for `{query}`:", ""]
            for hit in hits:
                lines.append(f"- **{hit.get('path')}** ({hit.get('kind')}, score {hit.get('score', 0):.1f})")
                if hit.get("summary"):
                    lines.append(f"  {hit['summary'][:160]}")
            body = "\n".join(lines)
        view = self.ensure_slot(key)
        view.begin_turn(query)
        view.complete_turn(answer=body, ok=True)

    def clear_slot(self, key: str) -> None:
        view = self._views.get(key)
        if view is not None:
            view.clear()

    def copy_text(self, key: str = "") -> str:
        view = self._views.get(key or self._active)
        return view.copy_text() if view is not None else ""

    def load_session(self, key: str, turns: list[dict[str, Any]], *, connection: str = "") -> None:
        """Render a saved session's turns into ``key``'s view (creating it)."""
        from dbaide.desktop.components.composer_options import POLICY_LABELS as _PL
        view = self.ensure_slot(key)
        view.clear()
        self._hint_shown = True
        for turn in turns:
            meta = turn.get("meta") or {}
            database = str(meta.get("database") or "")
            policy = str(meta.get("policy") or "safe_auto")
            meta_line = " · ".join(
                x for x in (connection, database or "auto", _PL.get(policy, policy)) if x
            )
            view.begin_turn(str(turn.get("question") or ""), meta=meta_line, placeholder=False)
            sql = str(turn.get("selected_sql") or "")
            status = str(turn.get("status") or "completed")
            view.complete_turn(
                answer=str(turn.get("answer_markdown") or ""),
                sql=sql,
                trace_events=turn.get("trace") or [],
                ok=status not in ("failed", "cancelled"),
                actions_widget=self._build_actions(sql, None),
            )

    # ── helpers ───────────────────────────────────────────────────────────────

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
