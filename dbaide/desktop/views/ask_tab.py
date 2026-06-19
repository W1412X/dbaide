from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button, discard_widget
from dbaide.desktop.components.conversation import ConversationView
from dbaide.desktop.components.empty_state import EmptyState
from dbaide.desktop.components.trace import close_trace_overlays, close_trace_overlays_for


class AskTab(QWidget):
    """Multi-session conversation surface.

    Each session (会话) gets its own ConversationView, kept in a QStackedWidget so a
    background run keeps updating its own view live even while another session is on
    screen. Methods that mutate a conversation take an explicit ``key`` (the session
    slot) so a run's progress/result always lands in the right place, regardless of
    which session is currently visible. ``key`` is the server session_id once known,
    or a temporary client key (``new:N``) for a brand-new, not-yet-saved chat.
    """

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

    # ── slot lifecycle ────────────────────────────────────────────────────────

    def ensure_slot(self, key: str) -> ConversationView:
        """Return the ConversationView for ``key``, creating + wiring it if needed."""
        view = self._views.get(key)
        if view is None:
            view = ConversationView()
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._views[key] = view
            self.stack.addWidget(view)
            if self._has_conn and not self._hint_shown:
                from dbaide.i18n import t as _t
                view.append_hint(_t("ask.hint"))
        return view

    def has_slot(self, key: str) -> bool:
        return key in self._views

    def set_active(self, key: str) -> None:
        """Show ``key``'s conversation (creating its view), or the empty page for
        ``""`` / when there's no connection."""
        close_trace_overlays(self)
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
            close_trace_overlays_for(existing)
            self.stack.removeWidget(existing)
            discard_widget(existing)
        self._views[new_key] = view
        if self._active == old_key:
            self._active = new_key

    def discard_slot(self, key: str) -> None:
        close_trace_overlays(self)
        view = self._views.pop(key, None)
        if view is not None:
            self.stack.removeWidget(view)
            discard_widget(view)
        if self._active == key:
            self._active = ""
            self.stack.setCurrentIndex(0)

    def view(self, key: str) -> ConversationView | None:
        return self._views.get(key)

    def reset_all(self) -> None:
        """Drop every slot (e.g. when the connection changes)."""
        close_trace_overlays(self)
        for key in list(self._views.keys()):
            view = self._views.pop(key)
            self.stack.removeWidget(view)
            discard_widget(view)
        self._active = ""
        self._hint_shown = False
        self.stack.setCurrentIndex(0)

    def turn_open(self, key: str) -> bool:
        view = self._views.get(key)
        return bool(view and view.has_open_turn())

    # ── connection / empty-state ──────────────────────────────────────────────

    def set_has_connection(self, has_connection: bool) -> None:
        if bool(self._has_conn) != bool(has_connection):
            close_trace_overlays(self)
        self._has_conn = has_connection
        self.set_active(self._active)

    def set_empty_context(self, has_connection: bool, has_model: bool) -> None:
        """Make the empty page reflect what's actually missing: no connection, no
        model, or just no chats yet (ready)."""
        from dbaide.i18n import t
        if not has_connection:
            self.empty.set_text(t("ask.empty_title"), t("ask.empty_subtitle"))
            self._empty_btn.setVisible(True)
        elif not has_model:
            self.empty.set_text(t("ask.empty_model_title"), t("ask.empty_model_subtitle"))
            self._empty_btn.setVisible(True)
        else:
            self.empty.set_text(t("ask.empty_ready_title"), t("ask.empty_ready_subtitle"))
            self._empty_btn.setVisible(False)

    # ── keyed conversation operations ─────────────────────────────────────────

    def begin_turn(self, key: str, question: str, *, connection: str, database: str,
                   attachments: list[dict] | None = None) -> None:
        meta = " · ".join(x for x in (connection, database or "auto") if x)
        self._hint_shown = True
        self.ensure_slot(key).begin_turn(question, meta=meta, attachments=attachments)

    def append_user(self, key: str, question: str, *, connection: str, database: str,
                    attachments: list[dict] | None = None) -> None:
        self.begin_turn(key, question, connection=connection, database=database, attachments=attachments)

    def append_activity(self, key: str, message: str) -> None:
        view = self._views.get(key)
        if view is not None and view.has_open_turn():
            view.append_trace(message)

    def append_answer_chunk(self, key: str, text: str) -> None:
        view = self._views.get(key)
        if view is not None and view.has_open_turn():
            view.append_answer_chunk(text)

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
            bar.submitted.connect(lambda reply, k=key: self.clarification_choice.emit(k, reply))

    def append_result(self, key: str, result: dict[str, Any]) -> None:
        if str(result.get("status") or "") == "wait_user":
            self.show_clarification(key, result)
            return
        status = str(result.get("status") or "completed")
        workflow_id = str(result.get("workflow_id") or "")
        ok = status not in ("failed", "cancelled")
        answer = result.get("answer_markdown") or result.get("answer_plaintext") or ""
        self.ensure_slot(key).complete_turn(
            answer=answer,
            trace_events=result.get("trace") or [],
            warnings=result.get("warnings") or None,
            errors=result.get("errors") or None,
            workflow_id=workflow_id,
            ok=ok,
            actions_widget=self._build_actions(
                answer,
                result.get("cli_command"),
                result.get("selected_sql"),
                charts=result.get("charts") or None,
                export_title=str(result.get("question") or ""),
            ),
            charts=result.get("charts") or None,
        )

    def append_note(self, key: str, title: str, body: str) -> None:
        view = self.ensure_slot(key)
        view.begin_turn("")
        body = f"**{title}**\n\n{body}"
        view.complete_turn(answer=body, ok=True, actions_widget=self._build_actions(body, None))

    def append_search_hits(self, key: str, query: str, hits: list[dict[str, Any]]) -> None:
        from dbaide.i18n import t as _t
        if not hits:
            body = _t("ask.search_no_results", query=query)
        else:
            lines = [_t("ask.search_results", n=len(hits), query=query), ""]
            for hit in hits:
                lines.append(f"- **{hit.get('path')}** ({hit.get('kind')}, score {hit.get('score', 0):.1f})")
                if hit.get("summary"):
                    lines.append(f"  {hit['summary'][:160]}")
            body = "\n".join(lines)
        view = self.ensure_slot(key)
        view.begin_turn(query)
        view.complete_turn(answer=body, ok=True, actions_widget=self._build_actions(body, None))

    def clear_slot(self, key: str) -> None:
        view = self._views.get(key)
        if view is not None:
            close_trace_overlays_for(view)
            view.clear()

    def copy_text(self, key: str = "") -> str:
        view = self._views.get(key or self._active)
        return view.copy_text() if view is not None else ""

    def load_session(self, key: str, turns: list[dict[str, Any]], *, connection: str = "") -> None:
        """Render a saved session's turns into ``key``'s view (creating it)."""
        view = self.ensure_slot(key)
        close_trace_overlays_for(view)
        view.clear()
        self._hint_shown = True
        view.begin_bulk_load()
        try:
            for turn in turns:
                meta = turn.get("meta") or {}
                database = str(meta.get("database") or "")
                meta_line = " · ".join(x for x in (connection, database or "auto") if x)
                # Restore attachment tags (db/table chips) if this turn had pinned context.
                attachments = turn.get("attachments") or None
                view.begin_turn(str(turn.get("question") or ""), meta=meta_line, placeholder=False,
                                attachments=attachments)
                status = str(turn.get("status") or "completed")
                view.complete_turn(
                    answer=str(turn.get("answer_markdown") or ""),
                    trace_events=turn.get("trace") or [],
                    ok=status not in ("failed", "cancelled"),
                    actions_widget=self._build_actions(
                        str(turn.get("answer_markdown") or ""),
                        None,
                        turn.get("selected_sql"),
                        charts=turn.get("charts") or None,
                        export_title=str(turn.get("question") or ""),
                    ),
                    charts=turn.get("charts") or None,
                )
        finally:
            view.end_bulk_load()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_actions(
        self,
        answer: str,
        cli_command: str | None,
        selected_sql: str | None = None,
        charts: list[dict[str, Any]] | None = None,
        *,
        export_title: str = "",
    ) -> QWidget | None:
        raw_answer = str(answer or "")
        answer = raw_answer.strip()
        selected_sql = str(selected_sql or "").strip()
        chart_list = [
            dict(c) for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")
        ]
        if not answer and not cli_command and not selected_sql:
            return None
        from dbaide.desktop.components.answer_document import answer_theme_payload
        from dbaide.desktop.components.icon_button import IconToolButton
        from dbaide.desktop.components.icons import svg_icon
        from dbaide.desktop.components.menu import _style_menu
        from dbaide.desktop.theme import Theme
        from dbaide.i18n import t as _t

        menu_items: list[tuple[str, str, object]] = []
        export_theme = answer_theme_payload()
        if answer:
            menu_items.append(("copy", _t("ask.copy_answer"), answer))
            menu_items.append((
                "download",
                _t("ask.export_answer_html"),
                lambda: self._open_answer_export_dialog(
                    raw_answer, chart_list, export_title, export_theme,
                ),
            ))
        if selected_sql:
            menu_items.append(("copy", _t("ask.copy_sql"), selected_sql))
        if cli_command:
            menu_items.append(("copy", _t("ask.copy_cli"), str(cli_command)))
        if not menu_items:
            return None

        btn = IconToolButton(
            svg_icon("more-horizontal", color=Theme.MUTED, size=14),
            _t("ask.more_actions"),
        )

        def _show_menu() -> None:
            from PyQt6.QtWidgets import QMenu
            menu = QMenu(btn)
            _style_menu(menu)
            for kind, label, payload in menu_items:
                icon_name = "download" if kind == "download" else "copy"
                action = menu.addAction(svg_icon(icon_name, color=Theme.TEXT_2, size=13), label)
                if callable(payload):
                    action.triggered.connect(lambda _checked=False, fn=payload: fn())
                else:
                    action.triggered.connect(
                        lambda _checked=False, p=str(payload): QApplication.clipboard().setText(p)
                    )
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

        btn.clicked.connect(_show_menu)
        return btn

    def _open_answer_export_dialog(
        self,
        answer: str,
        charts: list[dict[str, Any]] | None,
        export_title: str,
        theme: dict[str, Any],
    ) -> None:
        from dbaide.desktop.dialogs.answer_export import open_answer_export_dialog

        open_answer_export_dialog(
            self.window(),
            answer=answer,
            charts=charts,
            title=export_title,
            theme=theme,
        )
