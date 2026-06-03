from __future__ import annotations

import json
import sys
from typing import Any, Callable

from PyQt6.QtCore import Qt, QSettings, QThreadPool
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.composer import ComposerWidget
from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog
from dbaide.desktop.dialogs.settings import SettingsDialog
from dbaide.agent.progress_events import progress_label
from dbaide.desktop.theme import APP_STYLE
from dbaide.desktop.event_bus import (
    ASSETS_CHANGED,
    CONNECTIONS_CHANGED,
    JOINS_CHANGED,
    MODELS_CHANGED,
    QUERY_COMPLETED,
    EventBus,
)
from dbaide.i18n import t as _i18n_t
from dbaide.desktop.service import DesktopService


def _tab_label(tab_id: str) -> str:
    return _i18n_t("tab.ask") if tab_id == "Ask" else _i18n_t("tab.sql") if tab_id == "SQL" else tab_id
from dbaide.desktop.views.ask_tab import AskTab
from dbaide.desktop.views.right_panel import RightPanel
from dbaide.desktop.views.sidebar import Sidebar
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.topbar import TopBar
from dbaide.desktop.workers import CancelledError, ServiceWorker


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopService) -> None:
        super().__init__()
        self.service = service
        self.bus = EventBus()
        self.pool = QThreadPool.globalInstance()
        self.bootstrap: dict[str, Any] = {}
        self.schema_rows: list[dict[str, Any]] = []
        # ── Multi-run state ──────────────────────────────────────────────────
        # Each conversation (session) runs in its own worker; up to
        # max_concurrent_runs run at once, the rest queue. Everything is keyed by a
        # stable *slot key* — the server session_id once known, or a temporary
        # "new:N" key for a brand-new, not-yet-saved chat — so a run's progress and
        # result always land in the right session even when it isn't on screen.
        self._max_runs = self.service.cfg.max_concurrent_runs()
        self._runs: dict[str, ServiceWorker] = {}                 # slot key → active ask worker
        self._run_queue: list[tuple[str, dict[str, Any]]] = []    # (slot key, payload) waiting for a slot
        self._pending_resume: dict[str, dict[str, Any]] = {}      # slot key → clarification resume_state
        self._slot_trace: dict[str, list[dict[str, Any]]] = {}    # slot key → accumulated trace events
        self._slot_question: dict[str, str] = {}                  # slot key → last question (for resume label)
        self._slot_session: dict[str, str] = {}                   # slot key → server session_id (once known)
        self._new_counter = 0                                     # source of "new:N" temp keys
        self._active_key = ""                                     # the slot currently on screen
        # One-off non-conversation action (build assets / run SQL / etc.).
        self._oneoff_worker: ServiceWorker | None = None
        self._oneoff_action = ""
        self._building = False
        self._last_question = ""
        # The active chat session (会话) — the server id of the visible slot.
        self.current_session_id = ""
        self._settings = QSettings("DBAide", "DBAide")
        self._tab_names = ("Ask", "SQL")
        self.setWindowTitle("DBAide")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(APP_STYLE)
        self._build()
        # Restore whether the activity panel was collapsed last session.
        if str(self._settings.value("panel_visible", "true")).lower() == "false":
            self.right.setVisible(False)
        self._wire_bus()
        self.refresh_all()

    def _toggle_panel(self) -> None:
        self._set_panel_visible(not self.right.isVisible())

    def _show_panel(self) -> None:
        self._set_panel_visible(True)

    def _set_panel_visible(self, visible: bool) -> None:
        if visible == self.right.isVisible():
            return
        self.right.setVisible(visible)
        self._settings.setValue("panel_visible", "true" if visible else "false")

    def _wire_bus(self) -> None:
        """Central map of data-change events → who re-fetches. Components react to
        events instead of every action handler knowing what to refresh."""
        self.bus.subscribe(CONNECTIONS_CHANGED, lambda _p: self.refresh_all())
        self.bus.subscribe(ASSETS_CHANGED, lambda _p: self.refresh_all())
        # A model change only affects the model selector — don't reload the schema
        # tree / history / joins for the current connection.
        self.bus.subscribe(MODELS_CHANGED, lambda _p: self._refresh_models_only())
        self.bus.subscribe(JOINS_CHANGED, lambda _p: self.refresh_joins())
        self.bus.subscribe(QUERY_COMPLETED, lambda _p: self._on_query_completed())

    def _on_query_completed(self) -> None:
        # Chats (sessions) are the surfaced history now; refresh that list.
        self._load_sessions(self.current_connection())

    def _refresh_models_only(self) -> None:
        def on_loaded(bootstrap: dict[str, Any]) -> None:
            self.bootstrap = bootstrap
            models = bootstrap.get("models") or []
            default_model = str(bootstrap.get("default_model") or "default")
            self.composer.set_models(models, default_model)
        self._run_background("bootstrap", {}, on_loaded)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.topbar = TopBar()
        self.topbar.connection_changed.connect(self._connection_changed)
        self.topbar.database_changed.connect(self._database_changed)
        self.topbar.refresh.connect(self.refresh_all)
        self.topbar.build_assets.connect(self.build_assets)
        self.topbar.settings.connect(lambda: self.open_settings("connections"))
        self.topbar.toggle_panel.connect(self._toggle_panel)
        layout.addWidget(self.topbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("mainSplitter")
        self.body_splitter = body
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.sidebar.schema_preview.connect(self.preview_schema)
        self.sidebar.schema_selected.connect(self.open_schema_asset)
        self.sidebar.semantic_search_requested.connect(self.search_assets)
        self.sidebar.settings_requested.connect(lambda: self.open_settings("connections"))
        self.sidebar.chats.new_requested.connect(self.new_session)
        self.sidebar.chats.selected.connect(self.open_session)
        self.sidebar.chats.rename_requested.connect(self.rename_session)
        self.sidebar.chats.delete_requested.connect(self.delete_session)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 14, 16, 12)
        center_layout.setSpacing(12)
        tab_row = QHBoxLayout()
        self.tabbar = QTabBar()
        self.tabbar.setProperty("segmented", True)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setExpanding(False)
        for name in self._tab_names:
            self.tabbar.addTab(_tab_label(name))
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        tab_row.addWidget(self.tabbar)
        tab_row.addStretch(1)
        center_layout.addLayout(tab_row)

        self.stack = QStackedWidget()
        self.ask_tab = AskTab()
        self.sql_tab = SqlTab()
        self.ask_tab.empty_action.connect(self._empty_action)
        self.ask_tab.open_sql.connect(self.open_sql)
        self.ask_tab.clarification_choice.connect(self._submit_clarification)
        self.ask_tab.trace_requested.connect(self._reveal_turn_trace)
        self.sql_tab.run_requested.connect(lambda sql, _action: self.execute_sql(sql))
        self.stack.addWidget(self.ask_tab)
        self.stack.addWidget(self.sql_tab)
        center_layout.addWidget(self.stack, 1)

        self.composer = ComposerWidget()
        self.composer.submit_requested.connect(self.submit_composer)
        self.composer.stop_requested.connect(self.stop_task)
        self.composer.model_changed.connect(self._model_changed)
        center_layout.addWidget(self.composer)

        self.right = RightPanel()
        self.right.copy_trace_requested.connect(self.copy_trace)
        self.right.copy_conversation_requested.connect(self.copy_conversation)
        self.right.clear_trace_requested.connect(self.right.clear_all)
        # "Clear conversation" starts a fresh thread (resets the active session) so
        # the cleared view and the persisted session stay in sync.
        self.right.clear_conversation_requested.connect(self.new_session)
        self.right.history_selected.connect(self.load_history)
        self.right.history_preview.connect(self.preview_history)
        self.right.history_delete.connect(self.delete_history)
        self.right.joins_refresh_requested.connect(self.refresh_joins)
        self.right.joins_add_requested.connect(self._add_join)
        self.right.joins_update_requested.connect(self._update_join)
        self.right.joins_delete_requested.connect(self._delete_join)
        self.right.reveal_requested.connect(self._show_panel)

        body.addWidget(self.sidebar)
        body.addWidget(center)
        body.addWidget(self.right)
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        body.setCollapsible(2, True)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        self._apply_splitter_sizes(body)
        body.splitterMoved.connect(self._save_splitter_sizes)
        layout.addWidget(body, 1)

        self.setCentralWidget(root)
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Ready")
        # Land focus in the composer so the cursor is ready to type on launch
        # (and the topbar selectors don't show a stray focus ring at rest).
        self.composer.input.setFocus()

    def refresh_all(self) -> None:
        self.statusbar.showMessage("Loading…")
        self._run_background("bootstrap", {}, self._on_bootstrap_loaded)

    def _on_bootstrap_loaded(self, bootstrap: dict[str, Any]) -> None:
        try:
            self.bootstrap = bootstrap
            self._apply_bootstrap_ui()
            self.statusbar.showMessage("Ready")
        except Exception as exc:
            self.fail(exc)

    def _apply_bootstrap_ui(self) -> None:
        conns = self.bootstrap.get("connections") or []
        default = self.bootstrap.get("default_connection") or ""
        self.topbar.set_connections(conns, default)
        models = self.bootstrap.get("models") or []
        default_model = str(self.bootstrap.get("default_model") or "default")
        self.composer.set_models(models, default_model)
        conn_name = self.current_connection()
        has_conn = bool(conn_name)
        self.ask_tab.set_has_connection(has_conn)
        self.composer.set_disabled_no_connection(not has_conn)
        if has_conn:
            self._refresh_connection_context(conn_name)
        else:
            self.composer.set_placeholder(_i18n_t("composer.placeholder.no_conn"))

    def _run_background(
        self,
        action: str,
        payload: dict[str, Any],
        on_success: Callable[[Any], None],
        *,
        on_error: Callable[[object], None] | None = None,
    ) -> None:
        worker = ServiceWorker(self.service, action, payload)
        worker.signals.done.connect(lambda act, result: on_success(result) if act == action else None)
        if on_error:
            worker.signals.failed.connect(on_error)
        else:
            worker.signals.failed.connect(self._background_failed)
        self.pool.start(worker)

    def _background_failed(self, exc: object) -> None:
        self.toast(str(exc))

    def _default_splitter_sizes(self) -> list[int]:
        return [280, 780, 360]

    def _apply_splitter_sizes(self, splitter: QSplitter) -> None:
        defaults = self._default_splitter_sizes()
        saved_sizes = self._settings.value("splitter_sizes")
        sizes = defaults
        if saved_sizes:
            try:
                parsed = [int(x) for x in saved_sizes]
                if len(parsed) == 3 and parsed[0] >= 180 and parsed[1] >= 420:
                    sizes = parsed
            except (TypeError, ValueError):
                pass
        splitter.setSizes(sizes)

    def _save_splitter_sizes(self, *_args) -> None:
        sizes = self.body_splitter.sizes()
        if len(sizes) == 3 and sizes[0] >= 180 and sizes[1] >= 420:
            self._settings.setValue("splitter_sizes", sizes)

    def current_connection(self) -> str:
        return self.topbar.connection.current_value()

    def current_database(self) -> str:
        return self.topbar.database.current_value()

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            # The chat composer belongs to Ask only — hide it on the SQL tab (which
            # has its own Run action), so the SQL results get the full height.
            self.composer.setVisible(self._tab_names[index] == "Ask")

    def switch_tab(self, name: str) -> None:
        if name in self._tab_names:
            self.tabbar.setCurrentIndex(self._tab_names.index(name))

    def _connection_changed(self, _text: str) -> None:
        # Sessions are per-connection — drop the active session and clear the view so
        # one connection's conversation never bleeds into another. (Not fired during
        # bootstrap: set_connections blocks signals.)
        self._reset_all_slots()
        self.right.trace.clear_trace()
        conn = self.current_connection()
        if conn:
            self._refresh_connection_context(conn)

    def _reset_all_slots(self) -> None:
        """Cancel every in-flight run and drop all conversation slots — used when the
        connection changes (sessions are per-connection)."""
        for worker in list(self._runs.values()):
            if not worker.is_cancelled:
                worker.cancel()
        self._runs.clear()
        self._run_queue.clear()
        self._pending_resume.clear()
        self._slot_trace.clear()
        self._slot_question.clear()
        self._slot_session.clear()
        self.ask_tab.reset_all()
        self._active_key = ""
        self.current_session_id = ""
        self._refresh_run_status()

    def _database_changed(self, _text: str) -> None:
        database = self.current_database()
        self.toast(_i18n_t("toast.db_scope", scope=database or "auto"))

    def _refresh_connection_context(self, conn_name: str) -> None:
        conns = self.bootstrap.get("connections") or []
        self._load_schema(conn_name)
        self._load_sessions(conn_name)
        self.refresh_joins()
        asset_status = "missing"
        for c in conns:
            if c["name"] == conn_name:
                asset_status = c.get("asset_status") or "missing"
                break
        self.topbar.set_asset_status(asset_status)
        key = "composer.placeholder.ready" if asset_status == "ready" else "composer.placeholder.build"
        self.composer.set_placeholder(_i18n_t(key))

    def _load_sessions(self, name: str) -> None:
        if not name:
            self.sidebar.chats.load([])
            return

        def on_loaded(entries: list[dict[str, Any]]) -> None:
            self.sidebar.chats.load(entries or [])
            self.sidebar.chats.set_current(self.current_session_id)
        self._run_background("list_sessions", {"connection_name": name}, on_loaded)

    def _load_schema(self, name: str) -> None:
        self._run_background(
            "schema_tree",
            {"name": name},
            lambda rows: self._apply_schema_loaded(name, rows),
            on_error=lambda exc: self._apply_schema_error(name, str(exc)),
        )

    def _apply_schema_loaded(self, name: str, rows: list[dict[str, Any]]) -> None:
        if name != self.current_connection():
            return
        self.schema_rows = rows
        dbs = [row["name"] for row in self.schema_rows]
        self.topbar.set_databases(dbs)
        self.sidebar.load_schema(self.schema_rows)

    def _apply_schema_error(self, name: str, message: str) -> None:
        # Don't wipe the current connection's schema because an old one failed.
        if name != self.current_connection():
            return
        self.schema_rows = []
        self.sidebar.load_schema([], error=message)
        self.topbar.set_databases([])
        self.toast(f"Schema load failed: {message}")

    def _load_history(self, name: str) -> None:
        def on_loaded(entries: Any) -> None:
            # Drop stale responses for a connection the user already switched away from.
            if name == self.current_connection():
                self.right.load_history(entries)
        self._run_background("list_history", {"connection_name": name}, on_loaded)

    def refresh_joins(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.right.show_joins([])
            return
        try:
            result = self.service.dispatch("list_joins", {"connection_name": conn})
            self.right.show_joins(result.get("joins") or [])
        except Exception as exc:
            self.toast(str(exc))

    def _add_join(self, payload: dict[str, Any]) -> None:
        conn = self.current_connection()
        if not conn:
            return
        try:
            payload = {**payload, "connection_name": conn, "source": "user"}
            self.service.dispatch("add_join", payload)
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_saved"))
        except Exception as exc:
            self.toast(str(exc))

    def _update_join(self, payload: dict[str, Any]) -> None:
        conn = self.current_connection()
        if not conn:
            return
        try:
            self.service.dispatch("update_join", {**payload, "connection_name": conn})
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_updated"))
        except Exception as exc:
            self.toast(str(exc))

    def _delete_join(self, join_id: str) -> None:
        conn = self.current_connection()
        if not conn:
            return
        try:
            self.service.dispatch("delete_join", {"connection_name": conn, "id": join_id})
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_deleted"))
        except Exception as exc:
            self.toast(str(exc))

    def open_sql(self, sql: str) -> None:
        self.sql_tab.set_sql(sql)
        self.switch_tab("SQL")

    def submit_composer(self, question: str, policy: str) -> None:
        key = self._active_key
        # Active slot is awaiting a clarification reply → route there.
        if key and key in self._pending_resume:
            self._submit_clarification(key, question)
            return
        if not question:
            self.toast(_i18n_t("toast.enter_question"))
            return
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        if key and key in self._runs:
            self.toast(_i18n_t("toast.task_running"))
            return
        # A brand-new chat has no slot yet — mint one and make it active.
        if not key:
            key = self._new_slot_key()
            self._active_key = key
            self.current_session_id = ""
            self.ask_tab.set_active(key)
        self._last_question = question
        self._slot_question[key] = question
        database = self.current_database()
        self.composer.clear_input()
        self.ask_tab.append_user(key, question, connection=conn, database=database, policy=policy)
        # Fresh trace for this turn; show it live since this slot is the active one.
        self._slot_trace[key] = []
        self.right.trace.begin_live()
        self.right.focus_trace()
        self._start_ask(key, {
            "connection_name": conn,
            "question": question,
            "database": database,
            "execution_policy": policy,
            "show_trace": True,
            "session_id": self._slot_session.get(key, ""),
        })

    def _submit_clarification(self, key: str, reply: str) -> None:
        reply = str(reply or "").strip()
        if not reply:
            self.toast(_i18n_t("toast.enter_reply"))
            return
        resume_state = self._pending_resume.get(key)
        if not resume_state:
            # No pause for this slot — treat as a fresh question on the active slot.
            if key == self._active_key:
                self.submit_composer(reply, self.composer.policy())
            return
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        database = self.current_database()
        policy = self.composer.policy()
        original_question = str(resume_state.get("question") or self._slot_question.get(key, ""))
        # Consume the pause: queueing inside _start_ask guarantees the reply is never
        # lost even when every run slot is busy (it waits for a free slot).
        self._pending_resume.pop(key, None)
        if key == self._active_key:
            self.composer.clear_input()
        self.ask_tab.append_clarification_reply(key, reply)
        self.ask_tab.append_activity(key, f"User replied: {reply[:80]}")
        if key == self._active_key:
            self.right.focus_trace()
        self._start_ask(key, {
            "connection_name": conn,
            "question": original_question,
            "user_reply": reply,
            "resume_state": resume_state,
            "database": database,
            "execution_policy": policy,
            "show_trace": True,
            "session_id": self._slot_session.get(key, ""),
        })

    def _restore_composer_placeholder(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.composer.set_placeholder(_i18n_t("composer.placeholder.no_conn"))
            return
        conns = self.bootstrap.get("connections") or []
        asset_status = "missing"
        for c in conns:
            if c["name"] == conn:
                asset_status = c.get("asset_status") or "missing"
                break
        key = "composer.placeholder.ready" if asset_status == "ready" else "composer.placeholder.build"
        self.composer.set_placeholder(_i18n_t(key))

    def build_assets(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return

        conns = {c["name"]: c for c in self.bootstrap.get("connections") or []}
        load_profile = str((conns.get(conn) or {}).get("load_profile") or "production")
        default_mode = {"production": "light", "staging": "auto", "dev": "auto"}.get(load_profile, "light")
        default_workers = {"production": 1, "staging": 2, "dev": 4}.get(load_profile, 1)

        def on_loaded(result: dict[str, Any]) -> None:
            databases = list(result.get("databases") or [])
            if not databases:
                self.toast(_i18n_t("toast.no_databases"))
                return
            dialog = BuildAssetsDialog(
                connection_name=conn,
                databases=databases,
                load_profile=load_profile,
                default_profile_mode=default_mode,
                default_max_workers=default_workers,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = dialog.selected_databases()
            if not selected:
                self.toast(_i18n_t("toast.select_database"))
                return
            self._start_build_assets(conn, selected, dialog.build_options())

        self._run_background("list_databases", {"name": conn}, on_loaded)

    def _start_build_assets(self, conn: str, databases: list[str], options: dict[str, Any] | None = None) -> None:
        self.topbar.set_asset_status("building")
        self.topbar.set_global_status("Building assets", "building")
        self.right.trace.begin_live()
        self.right.focus_trace()
        payload: dict[str, Any] = {"name": conn}
        if databases:
            payload["databases"] = databases
        if options:
            payload.update(options)
        self.run_action("build_assets", payload)

    def add_connection(self, conn_type: str = "sqlite") -> None:
        self.open_settings("connections")

    def test_connection(self) -> None:
        conn = self.current_connection()
        if not conn:
            return
        conns = {c["name"]: c for c in self.bootstrap.get("connections") or []}
        payload = dict(conns.get(conn, {"name": conn, "type": "sqlite"}))
        self.run_action("test_connection", payload)

    def open_settings(self, page: str = "connections") -> None:
        try:
            resource_defaults = self.service.dispatch("resource_defaults", {})
        except Exception:
            resource_defaults = {}
        from dbaide.i18n import get_language
        dialog = SettingsDialog(
            connections=self.bootstrap.get("connections") or [],
            models=self.bootstrap.get("models") or [],
            default_connection=str(self.bootstrap.get("default_connection") or ""),
            default_model=str(self.bootstrap.get("default_model") or "default"),
            resource_defaults=resource_defaults,
            language=get_language(),
            parent=self,
            initial_page=page,
        )
        dialog.connection_saved.connect(lambda payload: self._settings_save_connection(dialog, payload))
        dialog.connection_deleted.connect(self._settings_delete_connection)
        dialog.connection_test.connect(lambda payload: self._settings_test_connection(dialog, payload))
        dialog.model_saved.connect(lambda payload: self._settings_save_model(dialog, payload))
        dialog.model_deleted.connect(self._settings_delete_model)
        dialog.model_test.connect(lambda payload: self._settings_test_model(dialog, payload))
        dialog.resource_saved.connect(self._settings_save_resources)
        dialog.language_changed.connect(self._change_language)
        dialog.exec()

    def _change_language(self, lang: str) -> None:
        # Language is applied at startup from config (UI + the model's answer
        # language), so a change persists and takes effect on the next launch —
        # we ask the user to restart rather than retranslate every live widget.
        from dbaide.i18n import normalize, t
        if normalize(lang) == self.service.cfg.ui_language():
            return
        try:
            self.service.cfg.set_ui_language(lang)
        except Exception as exc:
            self.fail(exc)
            return
        # Show the notice in the chosen language directly (i18n stays unchanged in-process).
        from dbaide.i18n import _STRINGS, DEFAULT_LANGUAGE
        code = normalize(lang)
        entry = _STRINGS.get("settings.restart_required", {})
        msg = entry.get(code) or entry.get(DEFAULT_LANGUAGE) or "Restart to apply."
        QMessageBox.information(self, "DBAide", msg)

    def _settings_save_resources(self, payload: dict[str, Any]) -> None:
        try:
            self.service.dispatch("save_resource_defaults", payload)
            # Apply the concurrency cap live; a higher cap can release queued runs.
            self._max_runs = self.service.cfg.max_concurrent_runs()
            self._drain_queue()
            self._refresh_run_status()
            self.toast(_i18n_t("toast.resources_saved"))
        except Exception as exc:
            self.fail(exc)

    def _model_changed(self, model_name: str) -> None:
        if not model_name:
            return
        try:
            result = self.service.dispatch("set_default_model", {"name": model_name})
            self.bus.emit(MODELS_CHANGED, {"model": model_name})
            # Label and cached bootstrap must reflect the newly-selected model,
            # not the one that was active at startup.
            active = result.get("model") or {}
            self.bootstrap["model"] = active
            self.bootstrap["default_model"] = model_name
            label = str(active.get("model") or model_name)
            self.toast(_i18n_t("toast.model", name=label))
        except Exception as exc:
            self.fail(exc)

    def _settings_save_connection(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        dialog.set_save_busy(True, target="connection")

        def on_done(_result: object) -> None:
            dialog.set_save_busy(False, target="connection")
            dialog._connections[payload["name"]] = dict(payload)
            if payload.get("make_default"):
                dialog._default_connection = payload["name"]
            dialog._reload_connection_list()
            self.toast(_i18n_t("toast.conn_saved"))
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": payload.get("name")})

        def on_fail(exc: object) -> None:
            dialog.set_save_busy(False, target="connection")
            dialog.show_test_result(False, str(exc), target="connection")

        self._run_background("save_connection", payload, on_done, on_error=on_fail)

    def _settings_delete_connection(self, name: str) -> None:
        try:
            self.service.dispatch("delete_connection", {"name": name})
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": name})
            self.toast(_i18n_t("toast.conn_removed"))
        except Exception as exc:
            self.fail(exc)

    def _settings_test_connection(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        dialog.set_test_busy(True, target="connection")

        def on_done(result: dict[str, Any]) -> None:
            dialog.set_test_busy(False, target="connection")
            dialog.show_test_result(True, str(result.get("message") or _i18n_t("toast.connection_ok")), target="connection")

        def on_fail(exc: object) -> None:
            dialog.set_test_busy(False, target="connection")
            dialog.show_test_result(False, str(exc), target="connection")

        self._run_background("test_connection", payload, on_done, on_error=on_fail)

    def _settings_save_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        dialog.set_save_busy(True, target="model")

        def on_done(_result: object) -> None:
            dialog.set_save_busy(False, target="model")
            dialog._models[payload["name"]] = dict(payload)
            if payload.get("make_default"):
                dialog._default_model = payload["name"]
            dialog._reload_model_list()
            self.toast(_i18n_t("toast.model_saved"))
            self.bus.emit(MODELS_CHANGED, {"model": payload.get("name")})

        def on_fail(exc: object) -> None:
            dialog.set_save_busy(False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("save_model", payload, on_done, on_error=on_fail)

    def _settings_delete_model(self, name: str) -> None:
        try:
            self.service.dispatch("delete_model", {"name": name})
            self.bus.emit(MODELS_CHANGED, {"model": name})
            self.toast(_i18n_t("toast.model_removed"))
        except Exception as exc:
            self.fail(exc)

    def _settings_test_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        dialog.set_test_busy(True, target="model")

        def on_done(result: dict[str, Any]) -> None:
            dialog.set_test_busy(False, target="model")
            dialog.show_test_result(bool(result.get("ok")), str(result.get("message") or "OK"), target="model")

        def on_fail(exc: object) -> None:
            dialog.set_test_busy(False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("test_model_profile", payload, on_done, on_error=on_fail)

    def execute_sql(self, sql: str) -> None:
        if not sql.strip():
            return
        self.run_action("execute_sql", {
            "connection_name": self.current_connection(),
            "database": self.current_database(),
            "sql": sql,
        })

    def inspect_schema(self, data: dict[str, Any]) -> None:
        self.open_schema_asset(data)

    def _show_asset(self, action: str, path: str) -> None:
        # Asset preview is a read-only file read — run it in the background so it
        # never flips the global status to "running" and works even while a query
        # is in flight (you can inspect tables mid-run).
        def on_loaded(res: dict[str, Any]) -> None:
            self.right.show_inspector(
                markdown=res.get("markdown") or "", doc=res.get("doc"), focus=True,
            )
        self._run_background(action, {"path": path}, on_loaded)

    def preview_schema(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if path:
            self._show_asset("preview_asset", path)

    def open_schema_asset(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if path:
            self._show_asset("asset_markdown", path)

    def load_asset(self, path: str) -> None:
        if path:
            self._show_asset("asset_markdown", path)

    def search_assets(self, query: str) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        self._last_question = query
        self.run_action("search_assets", {
            "connection_name": conn,
            "query": query,
        })

    # ── Chat sessions (会话) ──────────────────────────────────────────────────

    def new_session(self) -> None:
        """Open a fresh chat thread in its own slot. Other sessions keep running in
        the background; we just switch the view to a new, empty conversation."""
        key = self._new_slot_key()
        self._active_key = key
        self.current_session_id = ""
        self.ask_tab.set_active(key)
        self.ask_tab.set_has_connection(bool(self.current_connection()))
        self.right.trace.clear_trace()
        self.sidebar.chats.set_current("")
        self._sync_active_ui()
        self.composer.input.setFocus()

    def _reveal_turn_trace(self, key: object, events: object = None) -> None:
        """A turn's status chip was clicked — reveal that session's trace. ``events``
        is the turn's persisted trace (or None while it's still running, in which case
        the live/accumulated trace is already shown)."""
        if isinstance(events, list) and events and str(key) not in self._runs:
            self.right.show_trace(events)
        self.right.focus_trace()

    def open_session(self, session_id: str) -> None:
        """Switch to a saved session. If it's already loaded in a slot (e.g. running
        in the background), just show it; otherwise load it from disk."""
        conn = self.current_connection()
        if not conn or not session_id:
            return
        # Already loaded (possibly mid-run in the background) → just bring it forward.
        if self.ask_tab.has_slot(session_id):
            self._activate_slot(session_id)
            return

        def on_loaded(data: dict[str, Any]) -> None:
            sid = str(data.get("session_id") or session_id)
            turns = data.get("turns") or []
            self.ask_tab.load_session(sid, turns, connection=conn)
            self._slot_session[sid] = sid
            self._slot_trace[sid] = (turns[-1].get("trace") if turns else []) or []
            self._activate_slot(sid)
            self.switch_tab("Ask")

        self._run_background("load_session", {"connection_name": conn, "session_id": session_id}, on_loaded)

    def _activate_slot(self, key: str) -> None:
        """Bring slot ``key`` to the front: show its conversation + trace and sync the
        composer to whether it is idle / running / awaiting a reply."""
        self._active_key = key
        self.current_session_id = self._slot_session.get(key, "") or (key if not key.startswith("new:") else "")
        self.ask_tab.set_has_connection(bool(self.current_connection()))
        self.ask_tab.set_active(key)
        events = self._slot_trace.get(key, [])
        self.right.trace.show_events(events, live=key in self._runs)
        self._sync_chat_selection()
        self._sync_active_ui()

    def rename_session(self, session_id: str, title: str) -> None:
        conn = self.current_connection()
        if not conn or not session_id:
            return
        try:
            self.service.dispatch("rename_session", {
                "connection_name": conn, "session_id": session_id, "title": title,
            })
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Rename failed: {exc}")
            return
        self._load_sessions(conn)

    def delete_session(self, session_id: str) -> None:
        conn = self.current_connection()
        if not conn or not session_id:
            return
        try:
            self.service.dispatch("delete_session", {"connection_name": conn, "session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Delete failed: {exc}")
            return
        # Drop the slot for the deleted session (cancel its run if any).
        if self.ask_tab.has_slot(session_id):
            worker = self._runs.pop(session_id, None)
            if worker and not worker.is_cancelled:
                worker.cancel()
            for d in (self._pending_resume, self._slot_question, self._slot_session, self._slot_trace):
                d.pop(session_id, None)
            was_active = session_id == self._active_key
            self.ask_tab.discard_slot(session_id)
            if was_active:
                self._active_key = ""
                self.current_session_id = ""
                self.ask_tab.set_has_connection(bool(conn))
                self.right.trace.clear_trace()
                self._sync_active_ui()
        self._load_sessions(conn)

    def load_history(self, workflow_id: str) -> None:
        conn = self.current_connection()
        self.run_action("load_history", {"connection_name": conn, "workflow_id": workflow_id})
        self.switch_tab("Ask")

    def preview_history(self, workflow_id: str) -> None:
        conn = self.current_connection()
        if not conn or not workflow_id:
            return
        try:
            entry = self.service.dispatch("load_history", {
                "connection_name": conn,
                "workflow_id": workflow_id,
            })
            self.right.show_trace(entry.get("trace") or [])
            self.right.focus_trace()
            self.toast(f"Preview: {workflow_id}")
        except Exception as exc:
            self.toast(str(exc))

    def delete_history(self, workflow_id: str) -> None:
        conn = self.current_connection()
        if not conn or not workflow_id:
            return
        try:
            self.service.dispatch("delete_history", {
                "connection_name": conn,
                "workflow_id": workflow_id,
            })
            self._load_history(conn)
            self.toast(f"Deleted: {workflow_id}")
        except Exception as exc:
            self.toast(str(exc))

    # ── One-off (non-conversation) actions ────────────────────────────────────
    # build assets / run SQL / search / load history / preview / test connection.
    # Only one runs at a time, but it runs *alongside* conversation runs.

    def run_action(self, action: str, payload: dict[str, Any]) -> None:
        if self._oneoff_worker is not None:
            self.toast(_i18n_t("toast.task_running"))
            return
        self._oneoff_action = action
        if action == "build_assets":
            self._building = True
        if action == "execute_sql":
            self.sql_tab.set_running(True)
        worker = ServiceWorker(self.service, action, payload)
        worker.signals.progress.connect(self._on_oneoff_progress)
        worker.signals.done.connect(self._on_oneoff_done)
        worker.signals.failed.connect(self._on_oneoff_failed)
        self._oneoff_worker = worker
        self._sync_active_ui()
        self._refresh_run_status()
        self.pool.start(worker)

    def _on_oneoff_progress(self, message: object) -> None:
        # Only asset builds stream into the (active) trace panel.
        if self._oneoff_action != "build_assets":
            return
        self.statusbar.showMessage(progress_label(message if isinstance(message, dict) else str(message or "")))
        if isinstance(message, dict):
            self.right.trace.append_live_event(message)
        else:
            text = str(message or "").strip()
            if text:
                self.right.trace.append_live(text)

    def _on_oneoff_done(self, action: str, result: Any) -> None:
        self._oneoff_worker = None
        self._oneoff_action = ""
        self._building = False
        self.sql_tab.set_running(False)
        self._sync_active_ui()
        self._refresh_run_status()
        if action == "build_assets":
            self.right.trace.end_live()
            stats = result.get("stats", {}) or {}
            self.ask_tab.append_note(
                self._active_or_new_key(),
                _i18n_t("note.assets_built"),
                f"```json\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n```",
            )
            if not stats.get("estimated_queries"):
                self.bus.emit(ASSETS_CHANGED, {"instance": self.current_connection()})
            self.switch_tab("Ask")
            if stats.get("estimated_queries"):
                self.toast(f"≈{stats.get('estimated_queries')} queries (dry-run)")
            else:
                self.toast(
                    _i18n_t("toast.assets_built")
                    + f" · {stats.get('total_queries', 0)} queries · peak {stats.get('peak_inflight', 0)}"
                )
            return
        if action == "search_assets":
            self.right.show_search_hits(self._last_question, result)
            return
        if action in ("preview_asset", "asset_markdown"):
            self.right.show_inspector(
                markdown=result.get("markdown") or "", doc=result.get("doc"), focus=True,
            )
            return
        if action == "execute_sql":
            self.sql_tab.show_result(result)
            self.bus.emit(QUERY_COMPLETED, {"instance": self.current_connection()})
            return
        if action == "load_history":
            key = self._active_or_new_key()
            self.ask_tab.append_result(key, result)
            self._slot_trace[key] = list(result.get("trace") or [])
            if key == self._active_key:
                self.right.show_trace(result.get("trace") or [])
            self.switch_tab("Ask")
            return
        if action == "test_connection":
            self.toast(str(result.get("message") or _i18n_t("toast.connection_ok")))

    def _on_oneoff_failed(self, exc: object) -> None:
        action = self._oneoff_action
        self._oneoff_worker = None
        self._oneoff_action = ""
        self._building = False
        self.sql_tab.set_running(False)
        self.right.trace.end_live()
        self._sync_active_ui()
        self._refresh_run_status()
        if isinstance(exc, CancelledError):
            self.toast(_i18n_t("toast.cancelled"))
            return
        if action == "execute_sql":
            self.sql_tab.show_error(str(exc))
            self.toast(str(exc))
            return
        self.fail(exc, modal=action not in ("preview_asset", "search_assets"))

    # ── Conversation runs (one per session, capped + queued) ──────────────────

    def _new_slot_key(self) -> str:
        self._new_counter += 1
        return f"new:{self._new_counter}"

    def _active_or_new_key(self) -> str:
        """The active slot key, minting (and activating) a fresh one if there is none."""
        if not self._active_key:
            self._active_key = self._new_slot_key()
            self.current_session_id = ""
            self.ask_tab.set_active(self._active_key)
        return self._active_key

    def _start_ask(self, key: str, payload: dict[str, Any]) -> None:
        if len(self._runs) >= self._max_runs:
            self._run_queue.append((key, payload))   # waits for a free slot
            self.toast(_i18n_t("toast.run_queued"))
            self._sync_active_ui()
            self._refresh_run_status()
            return
        self._launch_ask(key, payload)

    def _launch_ask(self, key: str, payload: dict[str, Any]) -> None:
        worker = ServiceWorker(self.service, "ask", payload)
        worker.signals.progress.connect(lambda m, k=key: self._on_ask_progress(k, m))
        worker.signals.done.connect(lambda _a, r, k=key: self._on_ask_done(k, r))
        worker.signals.failed.connect(lambda e, k=key: self._on_ask_failed(k, e))
        self._runs[key] = worker
        self._sync_active_ui()
        self._refresh_run_status()
        self.pool.start(worker)

    def _on_ask_progress(self, key: str, message: object) -> None:
        if key not in self._runs:
            return
        if isinstance(message, dict):
            self._slot_trace.setdefault(key, []).append(message)
            self.ask_tab.append_activity_event(key, message)
            if key == self._active_key:
                self.statusbar.showMessage(progress_label(message))
                self.right.trace.append_live_event(message)
        else:
            text = str(message or "").strip()
            if text:
                self.ask_tab.append_activity(key, text)
                if key == self._active_key:
                    self.statusbar.showMessage(progress_label(text))
                    self.right.trace.append_live(text)

    def _on_ask_done(self, key: str, result: Any) -> None:
        self._runs.pop(key, None)
        server_id = str(result.get("session_id") or self._slot_session.get(key) or "")
        # A new chat's temp key becomes its server session_id once known.
        if server_id and server_id != key and not self.ask_tab.has_slot(server_id):
            self._migrate_slot(key, server_id)
            key = server_id
        self._slot_session[key] = server_id
        if result.get("trace"):
            self._slot_trace[key] = list(result.get("trace") or [])
        status = str(result.get("status") or "")
        if status == "wait_user":
            self._pending_resume[key] = result.get("resume_state") or {}
            self._slot_question[key] = str(result.get("question") or self._slot_question.get(key, ""))
            self.ask_tab.append_result(key, result)
            if key == self._active_key:
                if self.right.trace.is_empty():
                    self.right.show_trace(result.get("trace") or [])
                self.toast(_i18n_t("toast.waiting_reply"))
        elif status == "cancelled":
            self._pending_resume.pop(key, None)
            if self.ask_tab.turn_open(key):
                self.ask_tab.finish_turn_error(key, "**Cancelled**: Task stopped by user.")
            self.toast(_i18n_t("toast.cancelled"))
        else:
            self._pending_resume.pop(key, None)
            self.ask_tab.append_result(key, result)
            if key == self._active_key and self.right.trace.is_empty():
                self.right.show_trace(result.get("trace") or [])
            self.bus.emit(QUERY_COMPLETED, {"instance": self.current_connection()})
        if key == self._active_key:
            self.current_session_id = server_id or self.current_session_id
            self.right.trace.end_live()
        # A new session was persisted → refresh the Chats list so it appears.
        if server_id:
            self._load_sessions(self.current_connection())
        self._drain_queue()
        self._sync_active_ui()
        self._refresh_run_status()

    def _on_ask_failed(self, key: str, exc: object) -> None:
        self._runs.pop(key, None)
        self._pending_resume.pop(key, None)
        if self.ask_tab.turn_open(key):
            msg = ("**Cancelled**: Task stopped by user."
                   if isinstance(exc, CancelledError) else f"**Error**: {exc}")
            self.ask_tab.finish_turn_error(key, msg)
        if key == self._active_key:
            self.right.trace.end_live()
        self.toast(_i18n_t("toast.cancelled") if isinstance(exc, CancelledError) else str(exc))
        self._drain_queue()
        self._sync_active_ui()
        self._refresh_run_status()

    def _migrate_slot(self, old: str, new: str) -> None:
        self.ask_tab.remap(old, new)
        for d in (self._pending_resume, self._slot_question, self._slot_session, self._slot_trace, self._runs):
            if old in d:
                d[new] = d.pop(old)
        self._run_queue = [(new if k == old else k, p) for k, p in self._run_queue]
        if self._active_key == old:
            self._active_key = new

    def _drain_queue(self) -> None:
        while self._run_queue and len(self._runs) < self._max_runs:
            key, payload = self._run_queue.pop(0)
            if not self.ask_tab.has_slot(key):
                continue
            self._launch_ask(key, payload)

    def stop_task(self) -> None:
        key = self._active_key
        worker = self._runs.get(key) if key else None
        if worker and not worker.is_cancelled:
            worker.cancel()
            self.toast(_i18n_t("toast.cancelling"))
            return
        # Drop a still-queued active run.
        if key and any(k == key for k, _ in self._run_queue):
            self._run_queue = [(k, p) for k, p in self._run_queue if k != key]
            if self.ask_tab.turn_open(key):
                self.ask_tab.finish_turn_error(key, "**Cancelled**: Task stopped by user.")
            self._sync_active_ui()
            self._refresh_run_status()
            return
        if self._oneoff_worker and not self._oneoff_worker.is_cancelled:
            self._oneoff_worker.cancel()
            self.toast(_i18n_t("toast.cancelling"))
            return
        self.right.trace.end_live()
        self._sync_active_ui()
        self._refresh_run_status()

    def _sync_active_ui(self) -> None:
        """Reflect the *active* slot's run state in the composer + status."""
        if not self.current_connection():
            self.composer.set_disabled_no_connection(True)
            return
        self.composer.set_disabled_no_connection(False)
        key = self._active_key
        running = bool(key and key in self._runs)
        queued = any(k == key for k, _ in self._run_queue)
        waiting = bool(key and key in self._pending_resume)
        busy = running or queued or self._building
        self.composer.set_running(busy)
        if not busy:
            if waiting:
                self.composer.set_placeholder(_i18n_t("composer.placeholder.reply"))
            else:
                self._restore_composer_placeholder()

    def _refresh_run_status(self) -> None:
        active = len(self._runs) + len(self._run_queue)
        if self._building:
            self.topbar.set_global_status("Building assets", "building")
        elif active > 0:
            self.topbar.set_global_status(_i18n_t("status.runs_active", n=active), "running")
        else:
            self._restore_status_badge()
        keys = list(self._runs.keys()) + [k for k, _ in self._run_queue]
        running_ids = {(self._slot_session.get(k) or k) for k in keys}
        self.sidebar.chats.set_running(running_ids)
        # New (unsaved) chats that are running get an ephemeral row so they stay
        # reachable mid-run; dedupe by key, label with the question.
        pending: list[dict[str, Any]] = []
        seen: set[str] = set()
        for k in keys:
            if k.startswith("new:") and k not in seen:
                seen.add(k)
                title = (self._slot_question.get(k) or "").strip()
                pending.append({"key": k, "title": title[:60] or _i18n_t("session.new")})
        self.sidebar.chats.set_pending(pending)
        self._sync_chat_selection()

    def _sync_chat_selection(self) -> None:
        """Highlight the active slot's row — its server id, or the ephemeral key for a
        running new chat."""
        key = self._active_key
        sel = key if (key and key.startswith("new:")) else self.current_session_id
        self.sidebar.chats.set_current(sel)

    def _restore_status_badge(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.topbar.set_global_status("Idle", "idle")
            return
        asset_status = "missing"
        for c in self.bootstrap.get("connections") or []:
            if c["name"] == conn:
                asset_status = c.get("asset_status") or "missing"
                break
        self.topbar.set_asset_status(asset_status)

    def copy_trace(self) -> None:
        text = self.right.trace.copy_text()
        if not text.strip():
            self.toast(_i18n_t("toast.trace_empty"))
            return
        QApplication.clipboard().setText(text)
        self.toast(_i18n_t("toast.trace_copied"))

    def copy_conversation(self) -> None:
        text = self.ask_tab.copy_text()
        if not text.strip():
            self.toast(_i18n_t("toast.trace_empty"))
            return
        QApplication.clipboard().setText(text)
        self.toast(_i18n_t("toast.conversation_copied"))

    def _empty_action(self, action_id: str) -> None:
        if action_id == "settings":
            self.open_settings("connections")
        elif action_id == "refresh":
            self.refresh_all()

    def toast(self, message: str) -> None:
        self.statusbar.showMessage(message, 4000)

    def fail(self, exc: object, *, modal: bool = True) -> None:
        msg = f"**{type(exc).__name__}**: {exc}"
        key = self._active_or_new_key()
        if self.ask_tab.turn_open(key):
            self.ask_tab.finish_turn_error(key, msg)
        else:
            self.ask_tab.append_note(key, _i18n_t("note.error"), msg)
        if modal:
            QMessageBox.warning(self, "DBAide", str(exc))
        else:
            self.toast(str(exc))


class DBAideDesktop:
    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def run(self) -> None:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("DBAide")
        # Fusion makes global QSS apply consistently on macOS (native style ignores many label rules).
        app.setStyle("Fusion")
        window = MainWindow(self.service)
        window.show()
        app.exec()
