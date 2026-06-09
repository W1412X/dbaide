"""Run lifecycle controllers for the desktop UI.

``MainWindow`` owns widgets and wiring. These controllers own background run
lifecycles: one-off workbench actions and multi-slot Ask conversations. They
deliberately receive the window as a UI gateway so signal wiring stays stable
while mutable run-state rules live in one cohesive place.
"""

from __future__ import annotations

import json
from typing import Any

from dbaide.desktop.dialogs.message_dialog import confirm as dialog_confirm

from dbaide.agent.progress_events import progress_label
from dbaide.desktop.event_bus import ASSETS_CHANGED, QUERY_COMPLETED
from dbaide.desktop.ui_state import ComposerUiState, OneOffState, RunStatusUiState
from dbaide.desktop.workers import CancelledError
from dbaide.i18n import t as _i18n_t


class OneOffActionController:
    """Owns the single non-chat background action slot."""

    def __init__(self, window: Any) -> None:
        self.win = window

    def run_action(self, action: str, payload: dict[str, Any]) -> None:
        win = self.win
        if win._oneoff_worker is not None:
            win.toast(_i18n_t("toast.task_running"))
            return
        win.oneoff_state.begin(OneOffState(
            action=action,
            sql_doc=win._safe_doc("active_sql") if action in ("execute_sql", "explain_sql") else None,
            data_doc=win._safe_doc("active_data") if action in ("browse_table", "count_table") else None,
            sql=str(payload.get("sql") or win._last_sql or ""),
            connection=str(payload.get("connection_name") or payload.get("name") or win.current_connection() or ""),
            database=str(payload.get("database") or ""),
        ))
        sql_doc = win._safe_doc("oneoff_sql")
        data_doc = win._safe_doc("oneoff_data")
        if action in ("execute_sql", "explain_sql") and sql_doc is not None:
            win._ensure_ui_state().set_doc_running(sql_doc, True)
        if action in ("browse_table", "count_table") and data_doc is not None:
            win._ensure_ui_state().set_doc_running(data_doc, True)
        win.oneoff_state.attach_handle(win.tasks.start(
            action,
            payload,
            on_done=lambda result: self.on_done(action, result),
            on_failed=self.on_failed,
            on_progress=self.on_progress,
        ))
        win.conversation_controller.sync_work_ui()

    def on_progress(self, message: object) -> None:
        win = self.win
        if win._oneoff.action != "build_assets":
            return
        win._ensure_ui_state().statusbar_message(
            progress_label(message if isinstance(message, dict) else str(message or ""))
        )

    def on_done(self, action: str, result: Any) -> None:
        win = self.win
        state = win._oneoff
        expected_action = state.action
        sql_text = state.sql
        run_connection = state.connection
        run_database = state.database
        sql_doc = win._safe_doc("oneoff_sql")
        data_doc = win._safe_doc("oneoff_data")
        if sql_doc is not None:
            win._ensure_ui_state().set_doc_running(sql_doc, False)
        if data_doc is not None:
            win._ensure_ui_state().set_doc_running(data_doc, False)
        win.oneoff_state.finish()
        win.conversation_controller.sync_work_ui()
        action = expected_action or action
        if action == "build_assets":
            stats = result.get("stats", {}) or {}
            if run_connection != win.current_connection():
                if not stats.get("estimated_queries"):
                    win.bus.emit(ASSETS_CHANGED, {"instance": run_connection})
                return
            win.ask_tab.append_note(
                win.conversation_controller.active_or_new_key(),
                _i18n_t("note.assets_built"),
                f"```json\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n```",
            )
            if not stats.get("estimated_queries"):
                win.bus.emit(ASSETS_CHANGED, {"instance": win.current_connection()})
            win.switch_tab("Chat")
            if stats.get("estimated_queries"):
                win.toast(f"≈{stats.get('estimated_queries')} queries (dry-run)")
            else:
                win.toast(
                    _i18n_t("toast.assets_built")
                    + f" · {stats.get('total_queries', 0)} queries · peak {stats.get('peak_inflight', 0)}"
                )
            return
        if action == "search_assets":
            if run_connection != win.current_connection():
                return
            key = win.conversation_controller.active_or_new_key()
            win.ask_tab.set_active(key)
            win._active_key = key
            win.ask_tab.append_search_hits(key, win._last_question, result or [])
            win.switch_tab("Chat")
            return
        if action == "execute_sql":
            if isinstance(result, dict) and result.get("pending_confirmation"):
                warnings = "\n".join(str(w) for w in (result.get("warnings") or []))
                confirmed_sql = str(result.get("normalized_sql") or sql_text)
                sql_preview = confirmed_sql if len(confirmed_sql) <= 2000 else confirmed_sql[:2000] + "\n..."
                message = _i18n_t("risk.confirm_title")
                if warnings:
                    message += f"\n\n{_i18n_t('risk.warnings')}\n{warnings}"
                message += f"\n\n{_i18n_t('risk.sql')}\n{sql_preview}"
                if dialog_confirm(
                    win,
                    _i18n_t("risk.confirm_title"),
                    message,
                    ok_label=_i18n_t("risk.execute_anyway"),
                    cancel_label=_i18n_t("risk.cancel"),
                ):
                    self.run_action("execute_sql", {
                        "connection_name": run_connection,
                        "database": run_database,
                        "sql": confirmed_sql,
                        "confirmed_sql": confirmed_sql,
                    })
                return
            if sql_doc is not None:
                sql_doc.show_result(result)
            win._record_query(
                sql_text, ok=True,
                row_count=result.get("row_count"),
                elapsed_ms=result.get("elapsed_ms"),
                connection=run_connection,
                database=run_database,
            )
            win.bus.emit(QUERY_COMPLETED, {"instance": run_connection})
            return
        if action == "browse_table":
            if run_connection == win.current_connection() and data_doc is not None:
                data_doc.show_result(result)
            return
        if action == "count_table":
            if run_connection == win.current_connection() and data_doc is not None:
                data_doc.show_count(int(result.get("count") or 0))
            return
        if action == "explain_sql":
            if sql_doc is not None:
                sql_doc.show_result(result)
            return
        if action == "test_connection":
            if run_connection == win.current_connection():
                win.toast(str(result.get("message") or _i18n_t("toast.connection_ok")))

    def on_failed(self, exc: object) -> None:
        win = self.win
        state = win._oneoff
        action = state.action
        sql_text = state.sql
        run_connection = state.connection
        run_database = state.database
        sql_doc = win._safe_doc("oneoff_sql")
        data_doc = win._safe_doc("oneoff_data")
        if sql_doc is not None:
            win._ensure_ui_state().set_doc_running(sql_doc, False)
        if data_doc is not None:
            win._ensure_ui_state().set_doc_running(data_doc, False)
        win.oneoff_state.finish()
        win.conversation_controller.sync_work_ui()
        if isinstance(exc, CancelledError):
            win.toast(_i18n_t("toast.cancelled"))
            return
        stale_connection = bool(run_connection and run_connection != win.current_connection())
        if stale_connection and action not in ("execute_sql", "explain_sql"):
            return
        from dbaide.llm_errors import format_user_error
        friendly = format_user_error(exc)
        if action == "execute_sql":
            if sql_doc is not None:
                sql_doc.show_error(friendly)
            win._record_query(sql_text, ok=False, connection=run_connection, database=run_database)
            if not stale_connection:
                win.toast(friendly)
            return
        if action == "explain_sql":
            if sql_doc is not None:
                sql_doc.show_error(friendly)
            if not stale_connection:
                win.toast(friendly)
            return
        if action in ("browse_table", "count_table"):
            win.toast(friendly)
            return
        win.fail(exc, modal=action not in ("asset_markdown", "search_assets"))


class ConversationRunController:
    """Owns multi-slot Ask run state, queueing, pause/resume, and status UI."""

    def __init__(self, window: Any) -> None:
        self.win = window

    def new_slot_key(self) -> str:
        return self.win.run_state.new_slot_key()

    def active_or_new_key(self) -> str:
        win = self.win
        if not win._active_key:
            win._active_key = win.run_state.active_or_new_key()
            win.current_session_id = ""
            win.ask_tab.set_active(win._active_key)
        return win._active_key

    def start_ask(self, key: str, payload: dict[str, Any]) -> None:
        win = self.win
        if len(win._runs) >= win._max_runs:
            win.run_state.queue_run(key, payload)
            win.toast(_i18n_t("toast.run_queued"))
            self.sync_work_ui()
            return
        self.launch_ask(key, payload)

    def launch_ask(self, key: str, payload: dict[str, Any]) -> None:
        win = self.win
        handle = win.tasks.start(
            "ask",
            payload,
            on_done=lambda result, k=key: self.on_done(k, result),
            on_failed=lambda exc, k=key: self.on_failed(k, exc),
            on_progress=lambda message, k=key: self.on_progress(k, message),
            metadata={"slot": key},
        )
        win._runs[key] = handle
        win._slot_connection[key] = str(payload.get("connection_name") or win.current_connection() or "")
        self.sync_work_ui()

    def on_progress(self, key: str, message: object) -> None:
        win = self.win
        if key not in win._runs:
            return
        if isinstance(message, dict):
            if message.get("kind") == "answer_chunk":
                win.ask_tab.append_answer_chunk(key, str(message.get("text") or ""))
                return
            win._slot_trace.setdefault(key, []).append(message)
            win.ask_tab.append_activity_event(key, message)
            if key == win._active_key:
                win._ensure_ui_state().statusbar_message(progress_label(message))
        else:
            text = str(message or "").strip()
            if text:
                win.ask_tab.append_activity(key, text)
                if key == win._active_key:
                    win._ensure_ui_state().statusbar_message(progress_label(text))

    def on_done(self, key: str, result: Any) -> None:
        win = self.win
        if key not in win._runs:
            return
        run_connection = win._slot_connection.get(key) or win.current_connection()
        win._runs.pop(key, None)
        server_id = str(result.get("session_id") or win._slot_session.get(key) or "")
        if server_id and server_id != key and not win.ask_tab.has_slot(server_id):
            self.bind_slot_to_session(key, server_id)
            key = server_id
        win._slot_session[key] = server_id
        if result.get("trace"):
            win._slot_trace[key] = list(result.get("trace") or [])
        status = str(result.get("status") or "")
        if status == "wait_user":
            win._pending_resume[key] = result.get("resume_state") or {}
            win._slot_question[key] = str(result.get("question") or win._slot_question.get(key, ""))
            win.ask_tab.append_result(key, result)
            if key == win._active_key:
                win.toast(_i18n_t("toast.waiting_reply"))
        elif status == "cancelled":
            win._pending_resume.pop(key, None)
            if win.ask_tab.turn_open(key):
                win.ask_tab.finish_turn_error(key, _i18n_t("error.turn.cancelled"))
            win.toast(_i18n_t("toast.cancelled"))
        else:
            win._pending_resume.pop(key, None)
            win.ask_tab.append_result(key, result)
            win.bus.emit(QUERY_COMPLETED, {"instance": run_connection})
        if key == win._active_key:
            win.current_session_id = server_id or win.current_session_id
        if server_id and run_connection == win.current_connection():
            win._load_sessions(run_connection)
        if status != "wait_user":
            win._slot_connection.pop(key, None)
        self.drain_queue()
        self.sync_work_ui()

    def on_failed(self, key: str, exc: object) -> None:
        win = self.win
        if key not in win._runs:
            return
        win._runs.pop(key, None)
        win._slot_connection.pop(key, None)
        win._pending_resume.pop(key, None)
        win._slot_question.pop(key, None)
        win._slot_trace.pop(key, None)
        if win.ask_tab.turn_open(key):
            win.ask_tab.finish_turn_error(key, self._format_turn_error(exc))
        win.toast(self._user_error_message(exc))
        self.drain_queue()
        self.sync_work_ui()

    def _user_error_message(self, exc: object) -> str:
        if isinstance(exc, CancelledError):
            return _i18n_t("toast.cancelled")
        from dbaide.llm_errors import format_user_error
        return format_user_error(exc)

    def _format_turn_error(self, exc: object) -> str:
        if isinstance(exc, CancelledError):
            return _i18n_t("error.turn.cancelled")
        return _i18n_t("error.turn.error", message=self._user_error_message(exc))

    def bind_slot_to_session(self, temporary_key: str, session_id: str) -> None:
        self.win.ask_tab.remap(temporary_key, session_id)
        self.win.run_state.remap(temporary_key, session_id)

    def drain_queue(self) -> None:
        win = self.win
        while win._run_queue and len(win._runs) < win._max_runs:
            key, payload = win._run_queue.pop(0)
            if not win.ask_tab.has_slot(key):
                continue
            self.launch_ask(key, payload)

    def stop_task(self) -> None:
        win = self.win
        key = win._active_key
        worker = win._runs.get(key) if key else None
        if worker and not worker.is_cancelled:
            worker.cancel()
            win.toast(_i18n_t("toast.cancelling"))
            return
        if key and any(k == key for k, _ in win._run_queue):
            win.run_state.remove_queued(key)
            if win.ask_tab.turn_open(key):
                win.ask_tab.finish_turn_error(key, _i18n_t("error.turn.cancelled"))
            self.sync_work_ui()
            return
        if win._oneoff_worker and not win._oneoff_worker.is_cancelled:
            win._oneoff_worker.cancel()
            win.toast(_i18n_t("toast.cancelling"))
            return
        self.sync_work_ui()

    def sync_work_ui(self) -> None:
        """Composer + top-bar + chat run indicators — keep these in lockstep."""
        self.sync_active_ui()
        self.refresh_run_status()

    def sync_active_ui(self) -> None:
        win = self.win
        has_connection = bool(win.current_connection())
        waiting = win.run_state.is_active_waiting()
        assets_busy = win._assets_busy()
        busy = win.run_state.is_active_running() or win.run_state.is_active_queued() or assets_busy or win._building
        if not has_connection:
            placeholder = _i18n_t("composer.placeholder.no_conn")
        elif waiting and not busy:
            placeholder = _i18n_t("composer.placeholder.reply")
        elif assets_busy or win._building:
            placeholder = _i18n_t("composer.placeholder.building")
        else:
            placeholder = self.composer_ready_placeholder()
        win._ensure_ui_state().apply_composer(ComposerUiState(
            has_connection=has_connection,
            busy=busy,
            waiting_for_reply=waiting,
            placeholder=placeholder,
        ))

    def refresh_run_status(self) -> None:
        win = self.win
        selected = self.chat_selection_id()
        pending = self.pending_chat_rows()
        if win._assets_busy():
            text, state = win._current_asset_label() or _i18n_t("status.building"), "building"
        elif win._building:
            text, state = _i18n_t("status.building"), "building"
        elif win.run_state.active_count() > 0:
            text, state = _i18n_t("status.runs_active", n=win.run_state.active_count()), "running"
        else:
            win._restore_status_badge(force=True)
            win._ensure_ui_state().apply_chat_activity(win.run_state.running_ids(), pending, selected)
            return
        win._ensure_ui_state().apply_run_status(RunStatusUiState(
            topbar_text=text,
            topbar_state=state,
            running_ids=win.run_state.running_ids(),
            pending_rows=pending,
            selected_chat=selected,
        ))

    def chat_selection_id(self) -> str:
        key = self.win._active_key
        return key if (key and key.startswith("new:")) else self.win.current_session_id

    def pending_chat_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.win.run_state.pending_rows():
            title = str(row.get("title") or "") or _i18n_t("session.new")
            rows.append({**row, "title": title})
        return rows

    def composer_ready_placeholder(self) -> str:
        win = self.win
        conn = win.current_connection()
        if not conn:
            return _i18n_t("composer.placeholder.no_conn")
        asset_status = "missing"
        for item in win.bootstrap.get("connections") or []:
            if item["name"] == conn:
                asset_status = item.get("asset_status") or "missing"
                break
        key = "composer.placeholder.ready" if asset_status == "ready" else "composer.placeholder.build"
        return _i18n_t(key)
