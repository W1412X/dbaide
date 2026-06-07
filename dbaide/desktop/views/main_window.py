from __future__ import annotations

import sys
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.composer import ComposerWidget
from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog
from dbaide.desktop.dialogs.settings import SettingsDialog
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme, app_style
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
    return {
        "Assistant": _i18n_t("mode.assistant"),
        "Workbench": _i18n_t("mode.workbench"),
    }.get(tab_id, tab_id)
from dbaide.desktop.views.ask_tab import AskTab
from dbaide.desktop.dialogs.joins import JoinsDialog
from dbaide.desktop.dialogs.note_editor import NoteEditorDialog
from dbaide.desktop.views.joins_tab import JoinsTab
from dbaide.desktop.views.sidebar import Sidebar
from dbaide.desktop.views.workbench import WorkbenchView
from dbaide.desktop.dialogs.message_dialog import alert as dialog_alert, warn as dialog_warn
from dbaide.desktop.views.query_history import QueryHistoryPanel
from dbaide.history.query_store import QueryHistoryStore
from dbaide.desktop.views.topbar import TopBar
from dbaide.desktop.run_controllers import ConversationRunController, OneOffActionController
from dbaide.desktop.task_manager import TaskHandle, TaskManager
from dbaide.desktop.ui_state import (
    ConversationRunState,
    ModeUiState,
    OneOffRunState,
    OneOffState,
    UiStateBinder,
)


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopService) -> None:
        super().__init__()
        self.service = service
        self.bus = EventBus()
        self.tasks = TaskManager(service, self)
        self.run_state = ConversationRunState(max_runs=self.service.cfg.max_concurrent_runs())
        self.oneoff_state = OneOffRunState()
        self.oneoff_controller = OneOffActionController(self)
        self.conversation_controller = ConversationRunController(self)
        # (action, connection, label) — background asset/schema work tracked for the top bar.
        self._asset_work_stack: list[tuple[str, str, str]] = []
        self.bootstrap: dict[str, Any] = {}
        self.schema_rows: list[dict[str, Any]] = []
        self._last_question = ""
        # The active chat session (会话) — the server id of the visible slot.
        self.current_session_id = ""
        self._settings = QSettings("DBAide", "DBAide")
        self._tab_names = ("Assistant", "Workbench")
        self.setWindowTitle("DBAide")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(app_style())
        self._build()
        self.ui_state = UiStateBinder(self)
        self._install_shortcuts()
        self._wire_bus()
        self.refresh_all()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.tasks.cancel_all()
        super().closeEvent(event)

    def _ensure_run_state(self) -> ConversationRunState:
        if "run_state" not in self.__dict__:
            self.run_state = ConversationRunState()
        return self.run_state

    def _ensure_oneoff_state(self) -> OneOffRunState:
        if "oneoff_state" not in self.__dict__:
            self.oneoff_state = OneOffRunState()
        return self.oneoff_state

    def _ensure_ui_state(self) -> UiStateBinder:
        if "ui_state" not in self.__dict__:
            self.ui_state = UiStateBinder(self)
        return self.ui_state

    @property
    def _max_runs(self) -> int:
        return self._ensure_run_state().max_runs

    @_max_runs.setter
    def _max_runs(self, value: int) -> None:
        self._ensure_run_state().max_runs = int(value)

    @property
    def _runs(self) -> dict[str, TaskHandle]:
        return self._ensure_run_state().runs

    @_runs.setter
    def _runs(self, value: dict[str, TaskHandle]) -> None:
        self._ensure_run_state().runs = value

    @property
    def _run_queue(self) -> list[tuple[str, dict[str, Any]]]:
        return self._ensure_run_state().queue

    @_run_queue.setter
    def _run_queue(self, value: list[tuple[str, dict[str, Any]]]) -> None:
        self._ensure_run_state().queue = value

    @property
    def _pending_resume(self) -> dict[str, dict[str, Any]]:
        return self._ensure_run_state().pending_resume

    @_pending_resume.setter
    def _pending_resume(self, value: dict[str, dict[str, Any]]) -> None:
        self._ensure_run_state().pending_resume = value

    @property
    def _slot_trace(self) -> dict[str, list[dict[str, Any]]]:
        return self._ensure_run_state().slot_trace

    @_slot_trace.setter
    def _slot_trace(self, value: dict[str, list[dict[str, Any]]]) -> None:
        self._ensure_run_state().slot_trace = value

    @property
    def _slot_question(self) -> dict[str, str]:
        return self._ensure_run_state().slot_question

    @_slot_question.setter
    def _slot_question(self, value: dict[str, str]) -> None:
        self._ensure_run_state().slot_question = value

    @property
    def _slot_session(self) -> dict[str, str]:
        return self._ensure_run_state().slot_session

    @_slot_session.setter
    def _slot_session(self, value: dict[str, str]) -> None:
        self._ensure_run_state().slot_session = value

    @property
    def _slot_connection(self) -> dict[str, str]:
        return self._ensure_run_state().slot_connection

    @_slot_connection.setter
    def _slot_connection(self, value: dict[str, str]) -> None:
        self._ensure_run_state().slot_connection = value

    @property
    def _new_counter(self) -> int:
        return self._ensure_run_state().new_counter

    @_new_counter.setter
    def _new_counter(self, value: int) -> None:
        self._ensure_run_state().new_counter = int(value)

    @property
    def _active_key(self) -> str:
        return self._ensure_run_state().active_key

    @_active_key.setter
    def _active_key(self, value: str) -> None:
        self._ensure_run_state().active_key = str(value or "")

    @property
    def _oneoff(self) -> OneOffState:
        return self._ensure_oneoff_state().current

    @_oneoff.setter
    def _oneoff(self, value: OneOffState) -> None:
        self._ensure_oneoff_state().current = value

    @property
    def _oneoff_worker(self) -> TaskHandle | None:
        return self._ensure_oneoff_state().handle

    @_oneoff_worker.setter
    def _oneoff_worker(self, value: TaskHandle | None) -> None:
        self._ensure_oneoff_state().handle = value

    @property
    def _building(self) -> bool:
        return self._ensure_oneoff_state().building

    @_building.setter
    def _building(self, value: bool) -> None:
        self._ensure_oneoff_state().building = bool(value)

    def _install_shortcuts(self) -> None:
        """Global accelerators (⌘ on macOS maps from Ctrl in QKeySequence)."""
        def sc(seq: str, fn) -> None:
            QShortcut(QKeySequence(seq), self).activated.connect(fn)
        sc("Ctrl+1", lambda: self.tabbar.setCurrentIndex(0))   # Assistant
        sc("Ctrl+2", lambda: self.tabbar.setCurrentIndex(1))   # Workbench
        sc("Ctrl+T", self._shortcut_new_query)                 # new SQL editor
        sc("Ctrl+W", self._shortcut_close_doc)                 # close workbench doc

    def _shortcut_new_query(self) -> None:
        self.tabbar.setCurrentIndex(1)
        self.workbench.new_sql_editor()

    def _shortcut_close_doc(self) -> None:
        if self._current_mode() == "Workbench":
            self.workbench.close_current()

    def _current_mode(self) -> str:
        idx = self.tabbar.currentIndex()
        return self._tab_names[idx] if 0 <= idx < len(self._tab_names) else "Assistant"

    def _wire_bus(self) -> None:
        """Central map of data-change events → who re-fetches. Components react to
        events instead of every action handler knowing what to refresh."""
        self.bus.subscribe(CONNECTIONS_CHANGED, lambda _p: self.refresh_all())
        self.bus.subscribe(ASSETS_CHANGED, lambda _p: self.refresh_all())
        # A model change only affects the model selector — don't reload the schema
        # tree / history / joins for the current connection.
        self.bus.subscribe(MODELS_CHANGED, lambda _p: self._refresh_models_only())
        self.bus.subscribe(JOINS_CHANGED, lambda _p: self.refresh_joins())
        self.bus.subscribe(QUERY_COMPLETED, self._on_query_completed)

    def _on_query_completed(self, payload: dict[str, Any] | None = None) -> None:
        if isinstance(payload, dict):
            instance = str(payload.get("instance") or "")
            if instance and instance != self.current_connection():
                return
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
        self.topbar.refresh.connect(self.refresh_all)
        self.topbar.build_assets.connect(self.build_assets)
        self.topbar.settings.connect(lambda: self.open_settings("connections"))
        self.topbar.joins_requested.connect(self.open_joins)
        self.topbar.sync_schema_requested.connect(self.sync_schema)
        self.topbar.copy_conversation_requested.connect(self.copy_conversation)
        self.topbar.export_debug_requested.connect(self.export_debug_bundle)
        layout.addWidget(self.topbar)
        self.tabbar = self.topbar.mode_tabs
        mode_icons = {
            "Assistant": "message-circle",
            "Workbench": "terminal",
        }
        for name in self._tab_names:
            index = self.tabbar.addTab(
                svg_icon(mode_icons[name], color=Theme.TEXT_2, size=15),
                _tab_label(name),
            )
            self.tabbar.setTabToolTip(index, _tab_label(name))
        self.tabbar.currentChanged.connect(self._on_tab_changed)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("mainSplitter")
        self.body_splitter = body
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.sidebar.schema_preview.connect(self.preview_schema)
        self.sidebar.schema_selected.connect(self.open_schema_asset)
        self.sidebar.generate_sql.connect(self._generate_sql)
        self.sidebar.edit_note.connect(self._edit_note)
        self.sidebar.refresh_requested.connect(self._refresh_schema_node)
        self.sidebar.enrich_requested.connect(self._enrich_node)
        self.sidebar.semantic_search_requested.connect(self.search_assets)
        self.sidebar.settings_requested.connect(lambda: self.open_settings("connections"))
        self.sidebar.chats.new_requested.connect(self.new_session)
        self.sidebar.chats.selected.connect(self.open_session)
        self.sidebar.chats.rename_requested.connect(self.rename_session)
        self.sidebar.chats.delete_requested.connect(self.delete_session)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 12, 16, 12)
        center_layout.setSpacing(12)

        self.stack = QStackedWidget()
        # Assistant mode = the AI conversation; Workbench mode = the database client
        # (SQL editor + data browser). The two are deliberately separate surfaces.
        self.ask_tab = AskTab()
        self.ask_tab.empty_action.connect(self._empty_action)
        self.ask_tab.open_sql.connect(self.open_sql)
        self.ask_tab.clarification_choice.connect(self._submit_clarification)
        self.query_history_store = QueryHistoryStore()
        self.history_panel = QueryHistoryPanel()
        self.history_panel.open_editor_requested.connect(self._on_history_open_editor)
        self.history_panel.sql_selected.connect(self._on_history_select)
        self.history_panel.sql_run.connect(self._on_history_run)
        self.history_panel.clear_requested.connect(self._on_history_clear)
        self._last_sql = ""
        # Active documents the current one-off query writes back to (set when a run
        # starts, cleared if that document is closed mid-run).
        self._active_sql_doc = None
        self._active_data_doc = None
        self.workbench = WorkbenchView(self.history_panel)
        self.workbench.run_sql.connect(self._run_sql_from)
        self.workbench.explain_sql.connect(self._explain_from)
        self.workbench.browse_requested.connect(self._browse_from)
        self.workbench.count_requested.connect(self._count_from)
        self.workbench.ddl_requested.connect(self._ddl_from)
        self.workbench.doc_closed.connect(self._on_doc_closed)
        self.workbench.navigate_table.connect(self._open_table_by_name)
        self.workbench.navigate_fk.connect(self._navigate_fk)
        self.stack.addWidget(self.ask_tab)    # mode 0 — Assistant
        self.stack.addWidget(self.workbench)  # mode 1 — Workbench
        center_layout.addWidget(self.stack, 1)

        self.composer = ComposerWidget()
        self.composer.submit_requested.connect(self.submit_composer)
        self.composer.stop_requested.connect(self.conversation_controller.stop_task)
        self.composer.model_changed.connect(self._model_changed)
        self.composer.attach_requested.connect(self._show_attach_menu)
        center_layout.addWidget(self.composer)

        # The Joins manager (a niche feature) opens on demand from the topbar menu —
        # it no longer lives in a permanent side panel. The trace is now shown inline
        # in each conversation turn (click the "View agent trace" chip), so there is
        # no right-hand activity panel at all.
        self.joins = JoinsTab()
        self._joins_dialog: JoinsDialog | None = None
        # Connections whose base has been projected this session (avoid re-projecting on
        # every select) and a connection to auto-select once the next bootstrap applies.
        self._projected: set[str] = set()
        self._pending_select_conn = ""

        body.addWidget(self.sidebar)
        body.addWidget(center)
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        self._apply_splitter_sizes(body)
        body.splitterMoved.connect(self._save_splitter_sizes)
        layout.addWidget(body, 1)

        self.setCentralWidget(root)
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self._ensure_ui_state().statusbar_message("Ready")
        self._on_tab_changed(self.tabbar.currentIndex())
        # Land focus in the composer so the cursor is ready to type on launch
        # (and the topbar selectors don't show a stray focus ring at rest).
        self.composer.input.setFocus()

    def refresh_all(self) -> None:
        self._ensure_ui_state().statusbar_message("Loading…")
        self._run_background("bootstrap", {}, self._on_bootstrap_loaded, on_error=self._on_bootstrap_failed)

    def _on_bootstrap_loaded(self, bootstrap: dict[str, Any]) -> None:
        try:
            self.bootstrap = bootstrap
            self._apply_bootstrap_ui()
            self._ensure_ui_state().statusbar_message("Ready")
        except Exception as exc:
            self.fail(exc)

    def _on_bootstrap_failed(self, exc: object) -> None:
        self.conversation_controller.sync_work_ui()
        self._ensure_ui_state().statusbar_message(f"Load failed: {exc}")
        self.toast(str(exc))

    def _apply_bootstrap_ui(self) -> None:
        conns = self.bootstrap.get("connections") or []
        # Switch to a just-added connection now that it's in the list; the single
        # _refresh_connection_context below then loads (and lazily projects) its schema
        # with a visible loading state. (set_connections blocks signals, so selecting
        # the target here doesn't trigger a second, redundant schema load.)
        target = self.bootstrap.get("default_connection") or ""
        if self._pending_select_conn and any(c.get("name") == self._pending_select_conn for c in conns):
            target = self._pending_select_conn
        self._pending_select_conn = ""
        self.topbar.set_connections(conns, target)
        models = self.bootstrap.get("models") or []
        default_model = str(self.bootstrap.get("default_model") or "default")
        self.composer.set_models(models, default_model)
        conn_name = self.current_connection()
        has_conn = bool(conn_name)
        self.ask_tab.set_has_connection(has_conn)
        self.ask_tab.set_empty_context(has_conn, bool(models))
        self.conversation_controller.sync_work_ui()
        if has_conn:
            self._refresh_connection_context(conn_name)
        else:
            # No connection left (e.g. the current one was deleted) — clear the
            # per-connection views so a deleted connection's schema/sessions don't linger.
            self.schema_rows = []
            self.sidebar.load_schema([])
            self.sidebar.chats.load([])
            self.conversation_controller.sync_work_ui()

    _ASSET_STATUS_ACTIONS = frozenset({
        "build_assets",
        "project_instance",
        "refresh_instance",
        "enrich_table",
        "schema_tree",
    })

    _BACKGROUND_CANCEL_ACTIONS = _ASSET_STATUS_ACTIONS | frozenset({
        "execute_sql",
        "explain_sql",
        "browse_table",
        "count_table",
        "list_databases",
        "bootstrap",
    })

    def _asset_status_label(self, action: str) -> str:
        return {
            "build_assets": _i18n_t("status.building"),
            "project_instance": _i18n_t("schema.projecting"),
            "refresh_instance": _i18n_t("status.syncing"),
            "enrich_table": _i18n_t("status.enriching"),
            "schema_tree": _i18n_t("schema.loading"),
        }.get(action, _i18n_t("status.building"))

    def _asset_work_connection(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("name")
            or payload.get("connection_name")
            or self.current_connection()
            or ""
        )

    def _push_asset_work(self, action: str, payload: dict[str, Any]) -> None:
        conn = self._asset_work_connection(payload)
        label = self._asset_status_label(action)
        self._asset_work_stack.append((action, conn, label))
        if conn == self.current_connection():
            self.conversation_controller.sync_work_ui()

    def _pop_asset_work(self, action: str, payload: dict[str, Any]) -> None:
        conn = self._asset_work_connection(payload)
        for index in range(len(self._asset_work_stack) - 1, -1, -1):
            item_action, item_conn, _label = self._asset_work_stack[index]
            if item_action != action:
                continue
            if conn and item_conn and item_conn != conn:
                continue
            self._asset_work_stack.pop(index)
            break
        self.conversation_controller.sync_work_ui()

    def _current_asset_label(self, conn: str | None = None) -> str:
        conn = conn or self.current_connection()
        if not conn:
            return ""
        for _action, item_conn, label in reversed(self._asset_work_stack):
            if item_conn == conn:
                return label
        oneoff = self._oneoff
        if self._building and str(oneoff.connection or "") == conn:
            return _i18n_t("status.building")
        if self.service._build_active(conn):
            return _i18n_t("status.building")
        return ""

    def _assets_busy(self, conn: str | None = None) -> bool:
        conn = conn or self.current_connection()
        if not conn:
            return bool(self._asset_work_stack) or self._building
        if any(item_conn == conn for _action, item_conn, _label in self._asset_work_stack):
            return True
        oneoff = self._oneoff
        if self._building and str(oneoff.connection or "") == conn:
            return True
        return bool(self.service._build_active(conn))

    def _run_background(
        self,
        action: str,
        payload: dict[str, Any],
        on_success: Callable[[Any], None],
        *,
        on_error: Callable[[object], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> None:
        tracks_asset = action in self._ASSET_STATUS_ACTIONS
        if tracks_asset:
            self._push_asset_work(action, payload)

        def wrapped_success(result: Any) -> None:
            try:
                on_success(result)
            finally:
                if tracks_asset:
                    self._pop_asset_work(action, payload)

        def wrapped_error(exc: object) -> None:
            try:
                if on_error is not None:
                    on_error(exc)
                else:
                    self._background_failed(exc)
            finally:
                if tracks_asset:
                    self._pop_asset_work(action, payload)

        self.tasks.start(
            action,
            payload,
            on_done=wrapped_success,
            on_failed=wrapped_error,
            on_progress=on_progress,
        )

    def _background_failed(self, exc: object) -> None:
        self.toast(str(exc))

    def _default_splitter_sizes(self) -> list[int]:
        return [280, 1100]

    def _apply_splitter_sizes(self, splitter: QSplitter) -> None:
        defaults = self._default_splitter_sizes()
        saved_sizes = self._settings.value("splitter_sizes")
        sizes = defaults
        if saved_sizes:
            try:
                parsed = [int(x) for x in saved_sizes]
                if len(parsed) == 2 and parsed[0] >= 180 and parsed[1] >= 420:
                    sizes = parsed
            except (TypeError, ValueError):
                pass
        splitter.setSizes(sizes)

    def _save_splitter_sizes(self, *_args) -> None:
        sizes = self.body_splitter.sizes()
        if len(sizes) == 2 and sizes[0] >= 180 and sizes[1] >= 420:
            self._settings.setValue("splitter_sizes", sizes)

    def current_connection(self) -> str:
        return self.topbar.connection.current_value()

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self._ensure_ui_state().apply_mode(ModeUiState(index=index, mode=self._tab_names[index]))

    def switch_tab(self, name: str) -> None:
        if name == "Chat":
            self.tabbar.setCurrentIndex(0)
            return
        if name == "Workbench":
            self.tabbar.setCurrentIndex(1)
            self.workbench.focus_sql()
            return
        raise ValueError(f"Unknown top-level tab: {name!r}")

    def _connection_changed(self, _text: str) -> None:
        # Sessions are per-connection — drop the active session and clear the view so
        # one connection's conversation never bleeds into another. (Not fired during
        # bootstrap: set_connections blocks signals.)
        self._cancel_stale_background_work()
        self._reset_all_slots()
        # Table viewers show the old connection's data — close them. SQL editors are
        # portable text and stay; History re-loads for the new connection below.
        self.workbench.close_table_docs()
        conn = self.current_connection()
        if conn:
            self._refresh_connection_context(conn)

    def _reset_all_slots(self) -> None:
        """Cancel every in-flight run and drop all conversation slots — used when the
        connection changes (sessions are per-connection)."""
        for worker in list(self._runs.values()):
            if not worker.is_cancelled:
                worker.cancel()
        self.run_state.reset()
        self.ask_tab.reset_all()
        self.current_session_id = ""
        self.conversation_controller.sync_work_ui()

    def _cancel_stale_background_work(self) -> None:
        """Stop in-flight schema/build/SQL/browse tasks when switching connections."""
        worker = self._oneoff_worker
        if worker is not None and not worker.is_cancelled:
            worker.cancel()
        self.tasks.cancel_matching(lambda handle: handle.action in self._BACKGROUND_CANCEL_ACTIONS)
        self._asset_work_stack.clear()

    def _refresh_connection_context(self, conn_name: str) -> None:
        self._load_schema(conn_name)
        self._load_sessions(conn_name)
        self._refresh_query_history()
        self.refresh_joins()
        self.conversation_controller.sync_work_ui()

    def _load_sessions(self, name: str) -> None:
        if not name:
            self.sidebar.chats.load([])
            return

        def on_loaded(entries: list[dict[str, Any]]) -> None:
            if name != self.current_connection():
                return
            self.sidebar.chats.load(entries or [])
            self.conversation_controller.sync_work_ui()
        self._run_background("list_sessions", {"connection_name": name}, on_loaded)

    def _load_schema(self, name: str) -> None:
        if not name:
            self.sidebar.load_schema([])
            return
        loading = _i18n_t("schema.loading")
        self._ensure_ui_state().schema_loading(loading)
        self._run_background(
            "schema_tree",
            {"name": name},
            lambda rows: self._on_schema_rows(name, rows),
            on_error=lambda exc: self._apply_schema_error(name, str(exc)),
        )

    def _on_schema_rows(self, name: str, rows: list[dict[str, Any]]) -> None:
        if name != self.current_connection():
            return
        # No base document yet → build it from the live catalog (once per session),
        # keeping a visible loading state, then re-fetch the tree.
        if not rows and name not in self._projected:
            self._projected.add(name)
            projecting = _i18n_t("schema.projecting")
            self._ensure_ui_state().schema_loading(projecting)
            self._run_background(
                "project_instance", {"name": name},
                lambda _r: self._fetch_schema_after_project(name),
                on_error=lambda exc: self._project_failed(name, str(exc)),
                on_progress=lambda msg: self._on_schema_project_progress(name, msg),
            )
            return
        self._apply_schema_loaded(name, rows)

    def _on_schema_project_progress(self, name: str, message: object) -> None:
        if name != self.current_connection():
            return
        base = _i18n_t("schema.projecting")
        title = ""
        if isinstance(message, dict):
            title = str(message.get("title") or "").strip()
        elif message is not None:
            title = str(message).strip()
        text = f"{base} · {title}" if title else base
        self._ensure_ui_state().schema_loading(text, update=True)

    def _project_failed(self, name: str, message: str) -> None:
        self._projected.discard(name)  # allow a retry on the next select (e.g. DB was down)
        self._apply_schema_error(name, message)

    def _fetch_schema_after_project(self, name: str) -> None:
        if name != self.current_connection():
            return
        for conn in self.bootstrap.get("connections") or []:
            if conn.get("name") == name:
                conn["asset_status"] = "ready"
                break
        loading = _i18n_t("schema.loading")
        self._ensure_ui_state().schema_loading(loading, update=True)
        self._run_background(
            "schema_tree", {"name": name},
            lambda rows: self._apply_schema_loaded(name, rows),
            on_error=lambda exc: self._apply_schema_error(name, str(exc)),
        )

    def _apply_schema_loaded(self, name: str, rows: list[dict[str, Any]]) -> None:
        if name != self.current_connection():
            return
        self.schema_rows = rows
        self._ensure_ui_state().schema_loaded(self.schema_rows, self._schema_completion())
        if rows:
            for conn in self.bootstrap.get("connections") or []:
                if conn.get("name") == name:
                    conn["asset_status"] = "ready"
                    break
        self.conversation_controller.sync_work_ui()
        self._ensure_ui_state().statusbar_message(_i18n_t("status.ready"))

    def _schema_completion(self) -> dict[str, Any]:
        """Structured schema for context-aware SQL completion: database names, table
        names, columns per table, and tables per database — so the editor can cascade
        `db.` → its tables and `table.`/`db.table.` → its columns."""
        databases: list[str] = []
        tables: list[str] = []
        qualified_tables: list[str] = []
        columns_by_table: dict[str, list[str]] = {}
        columns_by_qualified: dict[str, list[str]] = {}
        tables_by_database: dict[str, list[str]] = {}
        column_types: dict[str, str] = {}
        for db in self.schema_rows:
            db_name = str(db.get("name") or "")
            if db_name:
                databases.append(db_name)
            for table in db.get("children", []):
                tname = str(table.get("name") or "")
                if not tname:
                    continue
                tables.append(tname)
                qname = f"{db_name}.{tname}" if db_name else tname
                if qname not in qualified_tables:
                    qualified_tables.append(qname)
                if db_name:
                    tables_by_database.setdefault(db_name, [])
                    if tname not in tables_by_database[db_name]:
                        tables_by_database[db_name].append(tname)
                cols: list[str] = []
                for c in table.get("children", []):
                    cname = str(c.get("name") or "")
                    if not cname:
                        continue
                    cols.append(cname)
                    dtype = str(c.get("data_type") or c.get("type") or "").strip()
                    if dtype:
                        column_types[f"{tname}.{cname}"] = dtype
                        column_types[f"{qname}.{cname}"] = dtype
                columns_by_table.setdefault(tname, [])
                for c in cols:
                    if c not in columns_by_table[tname]:
                        columns_by_table[tname].append(c)
                if cols:
                    columns_by_qualified[qname] = list(cols)
        return {
            "dialect": self._dialect(),
            "databases": databases,
            "tables": tables,
            "qualified_tables": qualified_tables,
            "columns_by_table": columns_by_table,
            "columns_by_qualified": columns_by_qualified,
            "tables_by_database": tables_by_database,
            "column_types": column_types,
        }

    def _apply_schema_error(self, name: str, message: str) -> None:
        # Don't wipe the current connection's schema because an old one failed.
        if name != self.current_connection():
            return
        self.schema_rows = []
        self._ensure_ui_state().schema_error(message)
        self.toast(f"Schema load failed: {message}")
        self.conversation_controller.sync_work_ui()
        self._ensure_ui_state().statusbar_message(f"Schema load failed: {message}")

    def refresh_joins(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.joins.load([])
            return
        try:
            result = self.service.dispatch("list_joins", {"connection_name": conn})
            self.joins.load(result.get("joins") or [])
        except Exception as exc:
            self.toast(str(exc))

    def open_joins(self) -> None:
        """Open the on-demand Joins manager (relocated here from the old side panel)."""
        if not self.current_connection():
            self.toast(_i18n_t("toast.select_connection"))
            return
        if self._joins_dialog is None:
            dialog = JoinsDialog(self.joins, parent=self)
            dialog.refresh_requested.connect(self.refresh_joins)
            dialog.add_requested.connect(self._add_join)
            dialog.update_requested.connect(self._update_join)
            dialog.delete_requested.connect(self._delete_join)
            self._joins_dialog = dialog
        self.refresh_joins()
        self._joins_dialog.show()
        self._joins_dialog.raise_()
        self._joins_dialog.activateWindow()

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
        self.tabbar.setCurrentIndex(1)
        self.workbench.open_sql(sql)

    def submit_composer(self, question: str) -> None:
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
        if self._assets_busy(conn):
            self.toast(_i18n_t("toast.assets_busy"))
            return
        # A brand-new chat has no slot yet — mint one and make it active.
        if not key:
            key = self.conversation_controller.new_slot_key()
            self._active_key = key
            self.current_session_id = ""
            self.ask_tab.set_active(key)
        self._last_question = question
        self._slot_question[key] = question
        # Database scope for the agent comes from composer attachments (schema_scope),
        # not a global selector — payload database is left empty for auto scope.
        attachments = self.composer.attachments()
        schema_scope = self._build_attached_scope(attachments) if attachments else {}
        self.composer.clear_attachments()
        self.composer.clear_input()
        self.ask_tab.append_user(key, question, connection=conn, database="", attachments=attachments)
        # Fresh trace for this turn (streamed inline into the turn's status chip).
        self._slot_trace[key] = []
        self.conversation_controller.start_ask(key, {
            "connection_name": conn,
            "question": question,
            "database": "",
            "session_id": self._slot_session.get(key, ""),
            "schema_scope": schema_scope,
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
                self.submit_composer(reply)
            return
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        if self._assets_busy(conn):
            self.toast(_i18n_t("toast.assets_busy"))
            return
        original_question = str(resume_state.get("question") or self._slot_question.get(key, ""))
        # Consume the pause: controller queueing guarantees the reply is never
        # lost even when every run slot is busy (it waits for a free slot).
        self._pending_resume.pop(key, None)
        if key == self._active_key:
            self.composer.clear_input()
        self.ask_tab.append_clarification_reply(key, reply)
        self.ask_tab.append_activity(key, f"User replied: {reply[:80]}")
        self.conversation_controller.start_ask(key, {
            "connection_name": conn,
            "question": original_question,
            "user_reply": reply,
            "resume_state": resume_state,
            "database": "",
            "session_id": self._slot_session.get(key, ""),
        })

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
            if conn != self.current_connection():
                return
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
        payload: dict[str, Any] = {"name": conn}
        if databases:
            payload["databases"] = databases
        if options:
            payload.update(options)
        self.oneoff_controller.run_action("build_assets", payload)

    def add_connection(self, conn_type: str = "sqlite") -> None:
        self.open_settings("connections")

    def test_connection(self) -> None:
        conn = self.current_connection()
        if not conn:
            return
        conns = {c["name"]: c for c in self.bootstrap.get("connections") or []}
        payload = dict(conns.get(conn, {"name": conn, "type": "sqlite"}))
        self.oneoff_controller.run_action("test_connection", payload)

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
            stream_answers=self.service.cfg.stream_answers(),
            debug_trace=self.service.cfg.debug_trace(),
            parent=self,
            initial_page=page,
        )
        dialog.connection_saved.connect(lambda payload: self._settings_save_connection(dialog, payload))
        dialog.connection_deleted.connect(lambda name: self._settings_delete_connection(dialog, name))
        dialog.connection_test.connect(lambda payload: self._settings_test_connection(dialog, payload))
        dialog.model_saved.connect(lambda payload: self._settings_save_model(dialog, payload))
        dialog.model_deleted.connect(lambda name: self._settings_delete_model(dialog, name))
        dialog.model_test.connect(lambda payload: self._settings_test_model(dialog, payload))
        dialog.resource_saved.connect(self._settings_save_resources)
        dialog.language_changed.connect(self._change_language)
        dialog.theme_changed.connect(self._change_theme)
        dialog.stream_answers_changed.connect(self._change_stream_answers)
        dialog.debug_trace_changed.connect(self._change_debug_trace)
        dialog.exec()

    def _change_debug_trace(self, enabled: bool) -> None:
        # Capture full LLM prompts/responses into the trace so a copied trace shows
        # every stage's context. Applied to THIS process immediately (next query) and
        # persisted for future launches.
        from dbaide.agent.llm_trace import set_tracing
        try:
            self.service.cfg.set_debug_trace(bool(enabled))
            set_tracing(bool(enabled))
            self.toast(_i18n_t("toast.debug_trace_on" if enabled else "toast.debug_trace_off"))
        except Exception as exc:  # noqa: BLE001
            self.toast(str(exc))

    def _change_stream_answers(self, enabled: bool) -> None:
        # Persisted to config; the backend reads it per request (next query streams or
        # not). No UI state to push — the conversation just renders whatever arrives.
        try:
            self.service.cfg.set_stream_answers(bool(enabled))
        except Exception as exc:  # noqa: BLE001
            self.toast(str(exc))

    def _change_theme(self, theme: str) -> None:
        from dbaide.desktop.theme import current_theme_name
        if theme == current_theme_name():
            return
        try:
            self.service.cfg.set_ui_theme(theme)
            self._show_restart_required()
        except Exception as exc:
            self.fail(exc)

    def _change_language(self, lang: str) -> None:
        from dbaide.i18n import normalize
        if normalize(lang) == self.service.cfg.ui_language():
            return
        try:
            self.service.cfg.set_ui_language(lang)
            self._show_restart_required(lang)
        except Exception as exc:
            self.fail(exc)

    def _show_restart_required(self, lang: str | None = None) -> None:
        from dbaide.i18n import DEFAULT_LANGUAGE, _STRINGS, normalize
        code = normalize(lang) if lang is not None else self.service.cfg.ui_language()
        entry = _STRINGS.get("settings.restart_required", {})
        msg = entry.get(code) or entry.get(DEFAULT_LANGUAGE) or "Restart DBAide to apply this setting."
        dialog_alert(self, "DBAide", msg)

    def _settings_save_resources(self, payload: dict[str, Any]) -> None:
        try:
            self.service.dispatch("save_resource_defaults", payload)
            # Apply the concurrency cap live; a higher cap can release queued runs.
            self._max_runs = self.service.cfg.max_concurrent_runs()
            self.conversation_controller.drain_queue()
            self.conversation_controller.sync_work_ui()
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
        self._ensure_ui_state().set_settings_busy(dialog, "save", True, target="connection")

        name = str(payload.get("name") or "")
        was_new = name not in {c.get("name") for c in (self.bootstrap.get("connections") or [])}

        def on_done(result: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "save", False, target="connection")
            dialog._connections[payload["name"]] = dict(payload)
            if payload.get("make_default"):
                dialog._default_connection = payload["name"]
            dialog._reload_connection_list()
            self.toast(_i18n_t("toast.conn_saved"))
            # A newly added connection → switch to it once the connection list reloads,
            # which loads (and lazily projects) its schema with a visible loading state.
            if was_new and name:
                self._pending_select_conn = name
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": payload.get("name")})

        def on_fail(exc: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "save", False, target="connection")
            dialog.show_test_result(False, str(exc), target="connection")

        self._run_background("save_connection", payload, on_done, on_error=on_fail)

    def sync_schema(self) -> None:
        """Re-sync the current connection's schema with the live database: detect
        added/removed/changed objects, update the base docs, cascade-delete notes of
        objects that are gone and flag stale enrichment. Runs in the background."""
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        self.toast(_i18n_t("toast.syncing"))

        def done(result: object) -> None:
            if conn != self.current_connection():
                return
            summary = (result or {}).get("summary", "") if isinstance(result, dict) else ""
            self.toast(_i18n_t("toast.synced", summary=summary))
            self.bus.emit(ASSETS_CHANGED, {"instance": conn})

        def fail(exc: object) -> None:
            if conn != self.current_connection():
                return
            self.toast(_i18n_t("toast.sync_failed", error=str(exc)))

        self._run_background("refresh_instance", {"name": conn}, done, on_error=fail)

    def _refresh_schema_node(self, node: dict[str, Any]) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        kind = str(node.get("kind") or "")
        if kind not in ("database", "table"):
            return
        _instance, database, table, _column = self._schema_path_parts(node)
        if not database:
            return
        target = ".".join(p for p in (database, table) if p)
        self.toast(_i18n_t("toast.syncing"))
        payload: dict[str, Any] = {"name": conn, "database": database}
        if table:
            payload["table"] = table
        self._ensure_ui_state().set_node_refreshing(node, True)

        def done(result: object) -> None:
            if conn != self.current_connection():
                self._ensure_ui_state().set_node_refreshing(node, False)
                return
            summary = (result or {}).get("summary", "") if isinstance(result, dict) else ""
            self._ensure_ui_state().set_node_refreshing(node, False)
            self.toast(_i18n_t("toast.synced", summary=summary or target))
            self.bus.emit(ASSETS_CHANGED, {"instance": conn})
            doc_path = f"{conn}.{database}.{table}" if table else f"{conn}.{database}"
            self._refresh_doc_if_open(doc_path)

        def fail(exc: object) -> None:
            if conn != self.current_connection():
                self._ensure_ui_state().set_node_refreshing(node, False)
                return
            self._ensure_ui_state().set_node_refreshing(node, False)
            self.toast(_i18n_t("toast.sync_failed", error=str(exc)))

        self._run_background("refresh_instance", payload, done, on_error=fail)

    def _enrich_node(self, node: dict[str, Any]) -> None:
        """Build the optional enrichment (LLM summary + sample + profile) for a table
        or whole database, from the schema-tree context menu. Runs in the background;
        a table enriches just itself (others untouched), a database enriches all its
        tables. Refreshes the tree on completion."""
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        kind = str(node.get("kind") or "")
        _instance, database, table, _column = self._schema_path_parts(node)
        if kind == "table" and database and table:
            target = f"{database}.{table}"
            action, payload = "enrich_table", {"connection_name": conn, "database": database, "table": table}
        elif kind == "database" and database:
            target = database
            action, payload = "build_assets", {"connection_name": conn, "databases": [database]}
        else:
            return
        self.toast(_i18n_t("toast.enriching", target=target))

        def done(_r: object) -> None:
            if conn != self.current_connection():
                return
            self.toast(_i18n_t("toast.enriched", target=target))
            self.bus.emit(ASSETS_CHANGED, {"instance": conn})

        def fail(exc: object) -> None:
            if conn != self.current_connection():
                return
            self.toast(_i18n_t("toast.enrich_failed", error=str(exc)))

        self._run_background(action, payload, done, on_error=fail)

    def _settings_delete_connection(self, dialog: SettingsDialog, name: str) -> None:
        try:
            self.service.dispatch("delete_connection", {"name": name})
            if not sip.isdeleted(dialog):
                dialog.remove_connection_entry(name)
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": name})
            self.toast(_i18n_t("toast.conn_removed"))
        except Exception as exc:
            self.fail(exc)

    def _settings_test_connection(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        self._ensure_ui_state().set_settings_busy(dialog, "test", True, target="connection")

        def on_done(result: dict[str, Any]) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="connection")
            dialog.show_test_result(True, str(result.get("message") or _i18n_t("toast.connection_ok")), target="connection")

        def on_fail(exc: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="connection")
            dialog.show_test_result(False, str(exc), target="connection")

        self._run_background("test_connection", payload, on_done, on_error=on_fail)

    def _settings_save_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        self._ensure_ui_state().set_settings_busy(dialog, "save", True, target="model")

        def on_done(_result: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "save", False, target="model")
            dialog._models[payload["name"]] = dict(payload)
            if payload.get("make_default"):
                dialog._default_model = payload["name"]
            dialog._reload_model_list()
            self.toast(_i18n_t("toast.model_saved"))
            self.bus.emit(MODELS_CHANGED, {"model": payload.get("name")})

        def on_fail(exc: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "save", False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("save_model", payload, on_done, on_error=on_fail)

    def _settings_delete_model(self, dialog: SettingsDialog, name: str) -> None:
        try:
            self.service.dispatch("delete_model", {"name": name})
            if not sip.isdeleted(dialog):
                dialog.remove_model_entry(name)
            self.bus.emit(MODELS_CHANGED, {"model": name})
            self.toast(_i18n_t("toast.model_removed"))
        except Exception as exc:
            self.fail(exc)

    def _settings_test_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        self._ensure_ui_state().set_settings_busy(dialog, "test", True, target="model")

        def on_done(result: dict[str, Any]) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="model")
            dialog.show_test_result(bool(result.get("ok")), str(result.get("message") or "OK"), target="model")

        def on_fail(exc: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("test_model_profile", payload, on_done, on_error=on_fail)

    def _run_sql_from(self, editor, sql: str) -> None:
        self._active_sql_doc = editor
        self.execute_sql(sql)

    def _explain_from(self, editor, sql: str) -> None:
        if not sql.strip():
            return
        self._active_sql_doc = editor
        self.oneoff_controller.run_action("explain_sql", {
            "connection_name": self.current_connection(),
            "database": "",
            "sql": sql,
        })

    def _browse_from(self, doc, payload: dict[str, Any]) -> None:
        self._active_data_doc = doc
        self.oneoff_controller.run_action("browse_table", payload)

    def _count_from(self, doc, payload: dict[str, Any]) -> None:
        self._active_data_doc = doc
        self.oneoff_controller.run_action("count_table", payload)

    def _ddl_from(self, doc, payload: dict[str, Any]) -> None:
        """Fetch the table's real CREATE TABLE DDL in the background and feed it to the
        document's Structure panel (non-blocking; the generated skeleton shows until)."""
        def on_loaded(result: dict[str, Any]) -> None:
            if doc is not None and not sip.isdeleted(doc):
                doc.show_ddl(str((result or {}).get("ddl") or ""))
        self._run_background("table_ddl", payload, on_loaded)

    def _on_doc_closed(self, widget) -> None:
        if widget is self._active_sql_doc:
            self._active_sql_doc = None
        if widget is self._active_data_doc:
            self._active_data_doc = None
        if widget is self._oneoff.sql_doc:
            self._oneoff.sql_doc = None
        if widget is self._oneoff.data_doc:
            self._oneoff.data_doc = None

    def _safe_doc(self, kind: str):
        """Return a workbench doc QWidget only if it is still alive (not deleteLater'd)."""
        slots = {
            "active_sql": (self, "_active_sql_doc"),
            "active_data": (self, "_active_data_doc"),
            "oneoff_sql": (self._oneoff, "sql_doc"),
            "oneoff_data": (self._oneoff, "data_doc"),
        }
        holder, attr = slots[kind]
        doc = getattr(holder, attr)
        if doc is not None and not sip.isdeleted(doc):
            return doc
        setattr(holder, attr, None)
        return None

    def execute_sql(self, sql: str) -> None:
        if not sql.strip():
            return
        self._last_sql = sql
        self.oneoff_controller.run_action("execute_sql", {
            "connection_name": self.current_connection(),
            "database": "",
            "sql": sql,
        })

    # ── Query history ─────────────────────────────────────────────────────────

    def _record_query(self, sql: str, *, ok: bool, row_count=None, elapsed_ms=None,
                      connection: str = "", database: str = "") -> None:
        if not (sql or "").strip():
            return
        conn = connection or self.current_connection()
        self.query_history_store.record(
            conn, sql, ok=ok,
            row_count=row_count, elapsed_ms=elapsed_ms,
            database=database or "",
        )
        if conn == self.current_connection():
            self._refresh_query_history()

    def _refresh_query_history(self) -> None:
        self.history_panel.load(self.query_history_store.recent(self.current_connection()))

    def _on_history_select(self, sql: str) -> None:
        self.tabbar.setCurrentIndex(1)
        self.workbench.open_sql(sql)

    def _on_history_run(self, sql: str) -> None:
        self.tabbar.setCurrentIndex(1)
        editor = self.workbench.open_sql(sql)
        self._active_sql_doc = editor
        self.execute_sql(sql)

    def _on_history_clear(self) -> None:
        self.query_history_store.clear(self.current_connection())
        self._refresh_query_history()

    def _on_history_open_editor(self) -> None:
        self.tabbar.setCurrentIndex(1)
        self.workbench.focus_sql()

    def _show_asset(self, path: str) -> None:
        # Read the doc in the background so it never flips the global status to
        # "running" and works even while a query is in flight.
        if not path:
            return
        self.tabbar.setCurrentIndex(1)  # Workbench
        title = path.split(".")[-1] if path else path
        self.workbench.open_doc(path, title, "")

        def on_loaded(res: dict[str, Any]) -> None:
            self.workbench.update_doc(path, res.get("markdown") or "")
        self._run_background("asset_markdown", {"path": path}, on_loaded)

    def preview_schema(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if not path:
            return
        self._show_asset(path)

    def _edit_note(self, node: dict[str, Any]) -> None:
        """Edit the user note for a db/table/column node (schema-tree pencil icon).

        The note is stored separately from the asset and shown inside the asset
        document; clearing the text removes it."""
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        kind = str(node.get("kind") or "")
        if kind not in ("database", "table", "column"):
            return
        _instance, database, table, column = self._schema_path_parts(node)
        body = {"connection_name": conn, "scope": kind,
                "database": database, "table": table, "column": column}
        try:
            res = self.service.dispatch("list_annotations", body)
            records = res.get("annotations") or []
            current = str(records[0].get("note")) if records else ""
        except Exception:
            current = ""
        qualified = ".".join(p for p in (database, table, column) if p) or conn
        label = f"{_i18n_t('notes.scope_' + kind)} · {qualified}"
        dialog = NoteEditorDialog(self, target_label=label, note=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.value()
        try:
            if text:
                self.service.dispatch("add_annotation", {**body, "note": text})
                self.toast(_i18n_t("toast.note_saved"))
            else:
                self.service.dispatch("delete_annotation", body)
                self.toast(_i18n_t("toast.note_deleted"))
        except Exception as exc:
            self.toast(str(exc))
            return
        # Refresh the affected document if it's open (a column note shows in its
        # parent table's doc; db/table notes show in their own doc).
        doc_path = f"{conn}.{database}" if kind == "database" else f"{conn}.{database}.{table}"
        self._refresh_doc_if_open(doc_path)

    def _refresh_doc_if_open(self, path: str) -> None:
        if not path or path not in getattr(self.workbench, "_doc_tabs", {}):
            return
        self._run_background(
            "asset_markdown", {"path": path},
            lambda res: self.workbench.update_doc(path, res.get("markdown") or ""),
        )

    def open_schema_asset(self, data: dict[str, Any]) -> None:
        # Double-clicking a table opens its data in the Data browser; other nodes
        # (databases, columns) fall back to the asset preview in a Workbench DocTab.
        path = str(data.get("path") or "")
        if str(data.get("kind") or "") == "table":
            _instance, database, table, _column = self._schema_path_parts(data)
            if database and table:
                conn = self.current_connection()
                # Opens (or focuses) a table document with Data + Structure sub-tabs.
                # Structure is built from the columns and FK data already in the node
                # — instant, no query; Data is the default view and loads its page 1.
                self.tabbar.setCurrentIndex(1)
                self.workbench.open_table(
                    conn, database, table, data.get("children") or [],
                    relations={
                        "foreign_keys": data.get("foreign_keys") or [],
                        "referenced_by": data.get("referenced_by") or [],
                    },
                    indexes=data.get("indexes") or [],
                )
                return
            # Fall through: table with a malformed/short path → show as doc
        if path:
            self._show_asset(path)

    def _dialect(self) -> str:
        conn = self.current_connection()
        for c in (self.bootstrap.get("connections") or []):
            if c.get("name") == conn:
                t = str(c.get("type", "")).lower()
                if t in ("mysql", "mariadb"):
                    return "mysql"
                if t in ("postgres", "postgresql"):
                    return "postgres"
                if t == "sqlite":
                    return "sqlite"
        return "generic"

    # ── Composer context attachment (the "+" button) ──────────────────────────

    def _show_attach_menu(self) -> None:
        """Build a db → table picker from the loaded schema; selecting attaches the
        asset as prompt context (cascading a table's database in automatically)."""
        from PyQt6.QtWidgets import QMenu
        from dbaide.desktop.components.menu import _style_menu
        menu = QMenu(self)
        _style_menu(menu)
        if not self.schema_rows:
            act = menu.addAction(_i18n_t("composer.attach_none"))
            act.setEnabled(False)
            menu.exec(self._attach_menu_pos())
            return
        for db in self.schema_rows:
            db_name = str(db.get("name") or "")
            tables = db.get("children") or []
            sub = menu.addMenu(db_name or "(database)")
            _style_menu(sub)
            # The database itself, as one attachable item.
            sub.addAction(
                f"{db_name}  (whole database)",
                lambda _=False, d=db: self._attach_node(d),
            )
            sub.addSeparator()
            for tnode in tables:
                if tnode.get("kind") != "table":
                    continue
                tname = str(tnode.get("name") or "")
                sub.addAction(tname, lambda _=False, tn=tnode: self._attach_node(tn))
        menu.exec(self._attach_menu_pos())

    def _attach_menu_pos(self):
        # Pop the menu just above the composer's attach button.
        btn = self.composer.attach_btn
        return btn.mapToGlobal(btn.rect().topLeft())

    def _attach_node(self, node: dict[str, Any]) -> None:
        """Attach a schema node (table or database) as prompt context. Attaching a
        table cascades its database in (deduplicated by path)."""
        kind = str(node.get("kind") or "")
        path = str(node.get("path") or "")
        name = str(node.get("name") or "")
        if not path:
            return
        if kind == "table":
            # Cascade: ensure the parent database is attached first (no duplicate).
            _instance, db_name, _table, _column = self._schema_path_parts(node)
            if db_name:
                db_path = ".".join(path.split(".")[:2])  # conn.database
                self.composer.add_attachment(
                    kind="database", path=db_path, name=db_name, database=db_name,
                )
            self.composer.add_attachment(kind="table", path=path, name=name, database=db_name)
        elif kind == "database":
            self.composer.add_attachment(kind="database", path=path, name=name, database=name)

    def _build_attached_scope(self, attachments: list[dict]) -> dict[str, Any]:
        """Turn composer attachments into a structured schema scope for the agent:
        {"databases": [name, ...], "tables": [{"database": db, "table": t}, ...]}.
        Paths are "connection.database[.table]"."""
        databases: list[str] = []
        tables: list[dict[str, str]] = []
        for att in attachments:
            parts = str(att.get("path") or "").split(".")
            kind = str(att.get("kind") or "")
            if kind == "database" and len(parts) >= 2:
                if parts[1] not in databases:
                    databases.append(parts[1])
            elif kind == "table" and len(parts) >= 3:
                entry = {"database": parts[1], "table": ".".join(parts[2:])}
                if entry not in tables:
                    tables.append(entry)
        return {"databases": databases, "tables": tables}

    def _generate_sql(self, node: dict[str, Any], kind: str) -> None:
        """Generate a starter statement for a table and open it in a new editor."""
        from dbaide.rendering.sql_templates import generate
        table = str(node.get("name") or "")
        if not table:
            return
        sql = generate(kind, table, node.get("children") or [], self._dialect())
        self.tabbar.setCurrentIndex(1)
        self.workbench.open_sql(sql)

    def _schema_path_parts(self, node: dict[str, Any]) -> tuple[str, str, str, str]:
        """Parse schema tree paths while allowing table names to contain dots.

        Paths are rendered as instance.database.table[.column]. For Postgres the
        table portion can itself be schema-qualified (e.g. public.users), so callers
        must not assume parts[2] is the whole table name.
        """
        kind = str(node.get("kind") or "")
        parts = [p for p in str(node.get("path") or "").split(".") if p]
        instance = parts[0] if len(parts) > 0 else ""
        database = parts[1] if len(parts) > 1 else ""
        if kind == "column":
            table = ".".join(parts[2:-1]) if len(parts) > 3 else (parts[2] if len(parts) > 2 else "")
            column = parts[-1] if len(parts) > 3 else ""
        elif kind == "table":
            table = ".".join(parts[2:]) if len(parts) > 2 else ""
            column = ""
        else:
            table = ""
            column = ""
        return instance, database, table, column

    def _find_table_node(self, table: str, database: str = "") -> dict[str, Any] | None:
        target = str(table or "").strip()
        target_db = str(database or "").strip()
        if "." in target and not target_db:
            prefix, rest = target.split(".", 1)
            # If the prefix matches a loaded database, treat it as db.table.
            if any(str(db.get("name") or "") == prefix for db in self.schema_rows):
                target_db, target = prefix, rest
        for db in self.schema_rows:
            db_name = str(db.get("name") or "")
            if target_db and db_name != target_db:
                continue
            for node in db.get("children") or []:
                if node.get("kind") != "table":
                    continue
                _instance, node_db, node_table, _column = self._schema_path_parts(node)
                if (node.get("name") == target or node_table == target) and (not target_db or node_db == target_db):
                    return node
        return None

    def _open_table_by_name(self, table: str, database: str = "") -> None:
        """Open a table by name (used by Structure-panel FK links). Searches the
        loaded schema for the matching node so we carry its columns + relations."""
        if not table:
            return
        if not database:
            current = self.workbench.tabs.currentWidget()
            database = str(getattr(current, "database", "") or "")
        node = self._find_table_node(table, database=database)
        if node is not None:
            self.open_schema_asset(node)
        else:
            self.toast(_i18n_t("toast.table_not_found", table=table))

    def _navigate_fk(self, ref_table: str, ref_column: str, value: object) -> None:
        """Open the referenced table filtered to the clicked FK value (data-cell
        'Open referenced row')."""
        from dbaide.adapters.base import quote_identifier
        from dbaide.rendering.table import _sql_literal
        current = self.workbench.tabs.currentWidget()
        current_db = str(getattr(current, "database", "") or "")
        node = self._find_table_node(ref_table, database=current_db)
        if node is None:
            self.toast(_i18n_t("toast.table_not_found", table=ref_table))
            return
        self.tabbar.setCurrentIndex(1)
        self.open_schema_asset(node)
        doc = self.workbench.tabs.currentWidget()
        if doc is None or not hasattr(doc, "browse_with_filter"):
            return
        where = f"{quote_identifier(ref_column, self._dialect())} = {_sql_literal(value)}"
        doc.browse_with_filter(where)

    def load_asset(self, path: str) -> None:
        if path:
            self._show_asset(path)

    def search_assets(self, query: str) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        self._last_question = query
        self.oneoff_controller.run_action("search_assets", {
            "connection_name": conn,
            "query": query,
        })

    # ── Chat sessions (会话) ──────────────────────────────────────────────────

    def new_session(self) -> None:
        """Open a fresh chat thread in its own slot. Other sessions keep running in
        the background; we just switch the view to a new, empty conversation."""
        key = self.conversation_controller.new_slot_key()
        self._active_key = key
        self.current_session_id = ""
        self.ask_tab.set_active(key)
        self.ask_tab.set_has_connection(bool(self.current_connection()))
        self.conversation_controller.sync_work_ui()
        self.composer.input.setFocus()

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
            if conn != self.current_connection():
                return
            sid = str(data.get("session_id") or session_id)
            turns = data.get("turns") or []
            self.ask_tab.load_session(sid, turns, connection=conn)
            self._slot_session[sid] = sid
            self._slot_trace[sid] = (turns[-1].get("trace") if turns else []) or []
            self._activate_slot(sid)
            self.switch_tab("Chat")

        self._run_background("load_session", {"connection_name": conn, "session_id": session_id}, on_loaded)

    def _activate_slot(self, key: str) -> None:
        """Bring slot ``key`` to the front: show its conversation and sync the
        composer to whether it is idle / running / awaiting a reply. (Each turn's
        trace travels inline with the conversation, so there's nothing else to swap.)"""
        self._active_key = key
        self.current_session_id = self._slot_session.get(key, "") or (key if not key.startswith("new:") else "")
        self.ask_tab.set_has_connection(bool(self.current_connection()))
        self.ask_tab.set_active(key)
        self.conversation_controller.sync_work_ui()

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
                self.conversation_controller.sync_work_ui()
        self._load_sessions(conn)

    def _restore_status_badge(self, *, force: bool = False) -> None:
        if not force and self._assets_busy():
            return
        self._ensure_ui_state().restore_connection_status(
            self.current_connection(),
            self.bootstrap.get("connections") or [],
        )

    def copy_conversation(self) -> None:
        text = self.ask_tab.copy_text()
        if not text.strip():
            self.toast(_i18n_t("toast.trace_empty"))
            return
        QApplication.clipboard().setText(text)
        self.toast(_i18n_t("toast.conversation_copied"))

    def export_debug_bundle(self) -> None:
        """Write a support ZIP (sanitized config, trace, log tail) under ~/.dbaide/debug/."""
        from dbaide.config import sanitize_config_data
        from dbaide.history.debug_bundle import create_desktop_debug_bundle
        from dbaide.observability.app_logging import tail_log_lines

        key = self._active_key
        context = {
            "connection_name": self.current_connection(),
            "session_id": self.current_session_id,
            "active_slot": key,
            "trace": list(self._slot_trace.get(key, [])) if key else [],
            "question": self._slot_question.get(key, "") if key else "",
        }
        try:
            path = create_desktop_debug_bundle(
                config=sanitize_config_data(self.service.cfg._data),
                context=context,
                log_tail=tail_log_lines(),
            )
        except OSError as exc:
            self.fail(exc, modal=False)
            return
        self.toast(_i18n_t("toast.debug_exported", path=str(path)))

    def _empty_action(self, action_id: str) -> None:
        if action_id == "settings":
            self.open_settings("connections")
        elif action_id == "refresh":
            self.refresh_all()

    def toast(self, message: str) -> None:
        self._ensure_ui_state().toast(message)

    def fail(self, exc: object, *, modal: bool = True) -> None:
        msg = f"**{type(exc).__name__}**: {exc}"
        key = self.conversation_controller.active_or_new_key()
        if self.ask_tab.turn_open(key):
            self.ask_tab.finish_turn_error(key, msg)
        else:
            self.ask_tab.append_note(key, _i18n_t("note.error"), msg)
        if modal:
            dialog_warn(self, "DBAide", str(exc))
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
