from __future__ import annotations

import sys
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QSettings, QTimer, QEvent, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
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
        "Dashboards": _i18n_t("mode.dashboards"),
    }.get(tab_id, tab_id)


def _fk_filter_where(ref_column: str, value: object, dialect: str) -> str:
    """WHERE clause to open a referenced row by its FK value. The column is quoted
    and the value rendered as a dialect-correct SQL literal — passing the dialect so
    backslash/quote escaping matches the target DB (generic escaping would mis-handle
    a backslash in a string FK value on MySQL)."""
    from dbaide.adapters.base import quote_identifier
    from dbaide.rendering.table import _sql_literal
    return f"{quote_identifier(ref_column, dialect)} = {_sql_literal(value, dialect=dialect)}"
from dbaide.desktop.views.ask_tab import AskTab
from dbaide.desktop.views.dashboards_view import DashboardsView
from dbaide.desktop.dialogs.joins import JoinsDialog
from dbaide.desktop.dialogs.note_editor import NoteEditorDialog
from dbaide.desktop.views.joins_tab import JoinsTab
from dbaide.desktop.views.sidebar import Sidebar
from dbaide.desktop.views.workbench import WorkbenchView
from dbaide.desktop.dialogs.file_dialogs import get_save_file_name
from dbaide.desktop.dialogs.message_dialog import alert as dialog_alert, warn as dialog_warn
from dbaide.desktop.views.query_history import QueryHistoryPanel
from dbaide.history.query_store import QueryHistoryStore
from dbaide.desktop.views.topbar import TopBar
from dbaide.desktop.run_controllers import ConversationRunController, OneOffActionController
from dbaide.desktop.task_manager import TaskHandle, TaskManager
from dbaide.desktop.ui_state import (
    BackgroundWorkState,
    ConversationRunState,
    ModeUiState,
    OneOffRunState,
    OneOffState,
    UiStateBinder,
)


class _ReleaseCheckNotifier(QObject):
    """Marshals release-check results from a worker thread onto the Qt main thread."""

    completed = pyqtSignal(object)


class _SslCheckNotifier(QObject):
    """Marshals HTTPS CA probe results from a worker thread onto the Qt main thread."""

    completed = pyqtSignal(object)


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopService) -> None:
        super().__init__()
        self.service = service
        self.bus = EventBus()
        self.tasks = TaskManager(service, self)
        self.run_state = ConversationRunState(max_runs=self.service.cfg.max_concurrent_runs())
        self.oneoff_state = OneOffRunState()
        self.background_work = BackgroundWorkState()
        self.oneoff_controller = OneOffActionController(self)
        self.conversation_controller = ConversationRunController(self)
        self.bootstrap: dict[str, Any] = {}
        self.schema_rows: list[dict[str, Any]] = []
        self._last_question = ""
        # The active chat session (会话) — the server id of the visible slot.
        self.current_session_id = ""
        self._settings = QSettings("DBAide", "DBAide")
        self._tab_names = ("Assistant", "Workbench", "Dashboards")
        self.setWindowTitle("DBAide")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(app_style())
        self._build()
        from dbaide.desktop.window_chrome import prepare_top_level_window

        self._integrated_title_bar = prepare_top_level_window(self, clear_title=True)
        self.ui_state = UiStateBinder(self)
        self._release_notifier = _ReleaseCheckNotifier(self)
        self._release_notifier.completed.connect(self._apply_release_check)
        self._release_check_in_progress = False
        self._ssl_notifier = _SslCheckNotifier(self)
        self._ssl_notifier.completed.connect(self._apply_ssl_check)
        self._ssl_check_in_progress = False
        self._install_shortcuts()
        self._wire_bus()
        self.refresh_all()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.tasks.cancel_all()
        # Give in-flight pool threads a moment to finish so their callbacks don't
        # fire on already-destroyed widgets.
        self.tasks.pool.waitForDone(2000)
        # The dashboard refresh runs on its own QThread (not the pool) — stop it too,
        # or Qt aborts with "QThread destroyed while running" on close.
        try:
            self.dashboard_tab.shutdown()
            for studio in self._dashboard_studios:
                studio.shutdown()
                studio.close()
        except Exception:
            pass
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_bg_finalized", False):
            self._bg_finalized = True
            from dbaide.desktop.window_chrome import apply_window_background

            apply_window_background(self)
        if getattr(self, "_integrated_title_bar", False) and not getattr(self, "_chrome_installed", False):
            self._chrome_installed = True
            from dbaide.desktop.window_chrome import install_top_level_chrome

            install_top_level_chrome(self, topbar=self.topbar)
        if not getattr(self, "_open_fade_played", False):
            self._open_fade_played = True
            self._play_open_fade()
        if not getattr(self, "_ssl_check_scheduled", False):
            self._ssl_check_scheduled = True
            QTimer.singleShot(400, self._check_https_certificates_at_startup)
        if not getattr(self, "_release_check_scheduled", False):
            self._release_check_scheduled = True
            QTimer.singleShot(800, self._check_for_updates_at_startup)

    def _play_open_fade(self) -> None:
        """Fade the whole window in on first show. windowOpacity is window-level
        compositing (unlike a QGraphicsOpacityEffect), so it is safe over the WebEngine
        answer view. Guarded so the window can never get stuck transparent."""
        try:
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: self.setWindowOpacity(1.0))
            self._open_fade = anim  # keep a reference so it isn't GC'd mid-flight
            anim.start()
            QTimer.singleShot(320, lambda: self.setWindowOpacity(1.0))
        except Exception:
            self.setWindowOpacity(1.0)

    def _check_for_updates_at_startup(self) -> None:
        self._start_release_check()
        self._release_timer = QTimer(self)
        self._release_timer.timeout.connect(self._start_release_check)
        self._release_timer.start(30 * 60 * 1000)

    def _start_release_check(self) -> None:
        if self._release_check_in_progress:
            return
        self._release_check_in_progress = True
        import threading

        notifier = self._release_notifier

        def worker() -> None:
            from dbaide.release_check import ReleaseCheckResult, check_for_update

            try:
                result = check_for_update()
            except Exception as exc:  # noqa: BLE001 — never leave UI stuck on “Checking…”
                result = ReleaseCheckResult(ok=False, error=str(exc))
            notifier.completed.emit(result)

        threading.Thread(target=worker, daemon=True, name="release-check").start()

    def _apply_release_check(self, result: object) -> None:
        self._release_check_in_progress = False
        from dbaide.app_info import app_version
        from dbaide.release_check import ReleaseCheckResult

        if not isinstance(result, ReleaseCheckResult):
            return
        self._release_check = result
        latest = result.latest
        latest_ver = latest.version if latest else ""
        release_url = latest.html_url if latest else ""
        if hasattr(self, "topbar"):
            self.topbar.set_update_available(
                result.ok and result.update_available,
                version=latest_ver,
                url=release_url,
            )
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None and not sip.isdeleted(dialog) and hasattr(dialog, "set_release_check_result"):
            dialog.set_release_check_result(
                ok=result.ok,
                current_version=app_version(),
                latest_version=latest_ver,
                update_available=result.update_available,
                ahead_of_release=result.ahead_of_release,
                release_url=release_url,
            )

    def _check_https_certificates_at_startup(self) -> None:
        self._start_ssl_check()

    def _start_ssl_check(self) -> None:
        if self._ssl_check_in_progress:
            return
        self._ssl_check_in_progress = True
        import threading

        notifier = self._ssl_notifier

        def worker() -> None:
            from dbaide.ssl_certs import HttpsCertCheck, check_https_certificates

            try:
                result = check_https_certificates(timeout=3.0)
            except Exception as exc:  # noqa: BLE001 — never leave the flag stuck
                result = HttpsCertCheck(False, str(exc))
            notifier.completed.emit(result)

        threading.Thread(target=worker, daemon=True, name="ssl-check").start()

    def _apply_ssl_check(self, result: object) -> None:
        self._ssl_check_in_progress = False
        from dbaide.ssl_certs import HttpsCertCheck

        if not isinstance(result, HttpsCertCheck) or result.ok:
            return
        detail = str(result.detail or "").strip()
        message = _i18n_t("startup.ssl.warning.message")
        if detail:
            message = f"{message}\n\n{detail}"
        dialog_warn(self, _i18n_t("startup.ssl.warning.title"), message)

    def _ensure_run_state(self) -> ConversationRunState:
        if "run_state" not in self.__dict__:
            self.run_state = ConversationRunState()
        return self.run_state

    def _ensure_oneoff_state(self) -> OneOffRunState:
        if "oneoff_state" not in self.__dict__:
            self.oneoff_state = OneOffRunState()
        return self.oneoff_state

    def _ensure_background_work(self) -> BackgroundWorkState:
        if "background_work" not in self.__dict__:
            self.background_work = BackgroundWorkState()
        return self.background_work

    @property
    def _asset_work_stack(self) -> list[tuple[str, str, str]]:
        return [
            (item.action, item.connection, item.label)
            for item in self._ensure_background_work().items
        ]

    @_asset_work_stack.setter
    def _asset_work_stack(self, value: list[tuple[str, str, str]]) -> None:
        state = self._ensure_background_work()
        state.clear()
        for action, connection, label in value or []:
            state.push(action, connection, label)

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

    # Per-slot state (question / trace / session_id / connection / pending_resume) lives
    # in ConversationRunState.slots and is accessed via its typed methods
    # (set_session/session_for, set_trace/trace_for, …). The old window-level dict
    # aliases were removed — go through run_state directly.

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
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(fn)
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
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.setAutoFillBackground(True)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.topbar = TopBar()
        self.topbar.connection_changed.connect(self._connection_changed)
        self.topbar.refresh.connect(self.refresh_all)
        self.topbar.build_assets.connect(self.build_assets)
        self.topbar.settings.connect(lambda: self.open_settings("connections"))
        self.topbar.joins_requested.connect(self.open_joins)
        self.topbar.backup_requested.connect(self.open_backup_manager)
        self.topbar.sync_schema_requested.connect(self.sync_schema)
        self.topbar.copy_conversation_requested.connect(self.copy_conversation)
        self.topbar.export_debug_requested.connect(self.export_debug_bundle)
        layout.addWidget(self.topbar)
        self.tabbar = self.topbar.mode_tabs
        mode_icons = {
            "Assistant": "message-circle",
            "Workbench": "terminal",
            "Dashboards": "table",
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
        body.setHandleWidth(4)
        self.sidebar = Sidebar()
        self.sidebar.schema_preview.connect(self.preview_schema)
        self.sidebar.schema_selected.connect(self.open_schema_asset)
        self.sidebar.generate_sql.connect(self._generate_sql)
        self.sidebar.edit_note.connect(self._edit_note)
        self.sidebar.refresh_requested.connect(self._refresh_schema_node)
        self.sidebar.enrich_requested.connect(self._enrich_node)
        self.sidebar.backup_requested.connect(self._backup_node)
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
        self.workbench.export_all_requested.connect(self._export_all_from)
        self.workbench.doc_closed.connect(self._on_doc_closed)
        self.workbench.navigate_table.connect(self._open_table_by_name)
        self.workbench.navigate_fk.connect(self._navigate_fk)
        self.workbench.doc_requested.connect(self._load_table_doc)
        self.dashboard_tab = DashboardsView(self.service)
        self._dashboard_studios: list[QWidget] = []
        self.ask_tab.pin_charts_requested.connect(self._on_pin_charts)
        self.ask_tab.build_dashboard_requested.connect(self._on_build_dashboard)
        self.stack.addWidget(self.ask_tab)    # mode 0 — Assistant
        self.stack.addWidget(self.workbench)  # mode 1 — Workbench
        self.stack.addWidget(self.dashboard_tab)  # mode 2 — Dashboards
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
        self.statusbar.setSizeGripEnabled(False)
        self.setStatusBar(self.statusbar)
        self._ensure_ui_state().statusbar_message("Ready")
        self._on_tab_changed(self.tabbar.currentIndex())
        # Land focus in the composer so the cursor is ready to type on launch
        # (and the topbar selectors don't show a stray focus ring at rest).
        self.composer.input.setFocus()

    def event(self, event) -> bool:  # noqa: N802 (Qt signature)
        """On Windows/Linux, Alt+letter toolbar mnemonics can swallow keys after Alt is
        pressed; return focus to the composer once Alt is released."""
        if sys.platform in ("win32", "linux"):
            if event.type() == QEvent.Type.KeyRelease and event.key() in (
                Qt.Key.Key_Alt, Qt.Key.Key_AltGr,
            ):
                fw = QApplication.focusWidget()
                if fw is not None and not isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)):
                    QTimer.singleShot(0, self.composer.input.setFocus)
            elif event.type() == QEvent.Type.ShortcutOverride:
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)):
                    if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() not in (
                        Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                    ):
                        event.accept()
        return super().event(event)

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
        from dbaide.llm_errors import format_user_error
        self.conversation_controller.sync_work_ui()
        msg = format_user_error(exc)
        self._ensure_ui_state().statusbar_message(msg)
        self.toast(msg)

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
        self._ensure_background_work().push(action, conn, label)
        if conn == self.current_connection():
            self.conversation_controller.sync_work_ui()

    def _pop_asset_work(self, action: str, payload: dict[str, Any]) -> None:
        conn = self._asset_work_connection(payload)
        self._ensure_background_work().pop(action, conn)
        self.conversation_controller.sync_work_ui()

    def _current_asset_label(self, conn: str | None = None) -> str:
        conn = conn or self.current_connection()
        if not conn:
            return ""
        label = self._ensure_background_work().label_for(conn)
        if label:
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
            return self._ensure_background_work().busy() or self._building
        if self._ensure_background_work().busy(conn):
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
    ) -> TaskHandle:
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

        return self.tasks.start(
            action,
            payload,
            on_done=wrapped_success,
            on_failed=wrapped_error,
            on_progress=on_progress,
        )

    def _background_failed(self, exc: object) -> None:
        from dbaide.llm_errors import format_user_error
        self.toast(format_user_error(exc))

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
            name = self._tab_names[index]
            if name == "Dashboards":
                self.dashboard_tab.reload()   # pick up boards/tiles pinned since last view
            self._ensure_ui_state().apply_mode(ModeUiState(index=index, mode=name))

    def _on_pin_charts(self, charts: object, question: str) -> None:
        """Pin selected charts from an answer onto a dashboard (new or existing)."""
        from dbaide.desktop.dialogs.pin_to_board import pin_charts as _pin_dialog

        chart_list = [c for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
        if not chart_list:
            return
        try:
            boards = self.service.dispatch("list_dashboards", {}).get("dashboards", [])
        except Exception as exc:  # noqa: BLE001
            self.toast(format_user_error(exc))
            return
        result = _pin_dialog(self, chart_list, boards)
        if result is None:
            return
        picked, dash_id, dash_name = result
        conn = self.current_connection()
        try:
            # resolve the target board ONCE so all charts land on the same board
            # (and a failed first pin can't spawn duplicate same-named boards)
            if not dash_id and dash_name:
                created = self.service.dispatch("create_dashboard", {"name": dash_name}).get("dashboard") or {}
                dash_id = str(created.get("id") or "")
            if not dash_id:
                return
            for chart in picked:
                self.service.dispatch("pin_chart", {
                    "name": str(chart.get("title") or _i18n_t("conversation.chart")),
                    "connection_name": conn,
                    "nl_question": str(question or ""),
                    "sql": str(chart.get("source_sql") or ""),
                    "chart_plan": chart.get("chart_plan") if isinstance(chart.get("chart_plan"), dict) else None,
                    "chart_spec": chart,
                    "row_count": int(chart.get("row_count") or 0),
                    "dashboard_id": dash_id,
                })
        except Exception as exc:  # noqa: BLE001
            self.toast(format_user_error(exc))
            return
        self.toast(_i18n_t("toast.pinned", n=len(picked)))
        self.dashboard_tab.reload()

    def _on_build_dashboard(self, charts: object, question: str, selected_sql: str = "") -> None:
        """Open the AI dashboard studio seeded with this answer's analysis."""
        chart_list = [c for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
        conn = self.current_connection()
        # prefer each chart's own source SQL; fall back to the answer's selected SQL
        # (older charts predate source-SQL provenance — the agent re-parameterizes anyway)
        context = [
            {"nl_question": str(question or ""),
             "sql": str(c.get("source_sql") or selected_sql or ""),
             "chart_plan": c.get("chart_plan") if isinstance(c.get("chart_plan"), dict) else {},
             "title": str(c.get("title") or ""), "connection_name": conn}
            for c in chart_list
            if c.get("source_sql") or str(selected_sql or "").strip()
        ]
        if not context:
            self.toast(_i18n_t("app.no_source_sql"))
            return
        from dbaide.desktop.views.parametric_dashboard import ParametricDashboardStudio
        studio = ParametricDashboardStudio(self.service)
        studio.setWindowTitle(_i18n_t("app.window_title"))
        studio.resize(1040, 760)
        studio.show()
        self._dashboard_studios.append(studio)
        studio.start(name=str(question or _i18n_t("app.window_title")), connection_name=conn,
                     context=context, instruction=_i18n_t("app.default_instruction"))

    def animate_page_in(self, index: int) -> None:
        """Subtle fade-in for the page that just became current. Skipped for pages that
        host a QWebEngineView — a QGraphicsOpacityEffect blacks out WebEngine surfaces —
        and always cleaned up so the effect never lingers (perf) or leaves a page dim."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        page = self.stack.widget(index) if hasattr(self, "stack") else None
        if page is None:
            return
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            if page.findChild(QWebEngineView) is not None:
                return
        except Exception:
            pass
        try:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", page)
            anim.setDuration(170)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _clear() -> None:
                if page.graphicsEffect() is effect:
                    page.setGraphicsEffect(None)
            anim.finished.connect(_clear)
            self._page_anim = anim  # keep alive
            anim.start()
            QTimer.singleShot(300, _clear)  # safety: never leave the page dimmed
        except Exception:
            page.setGraphicsEffect(None)

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
        self._ensure_background_work().clear()

    def _refresh_connection_context(self, conn_name: str) -> None:
        self.sidebar.reset_live_updates()
        self.sidebar.clear_schema_expansion()
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
            self._start_project_instance(name)
            return
        self._apply_schema_loaded(name, rows)

    def _start_project_instance(self, name: str) -> None:
        projecting = _i18n_t("schema.projecting")
        self._ensure_ui_state().schema_build_progress_start(projecting)

        def on_progress(message: object) -> None:
            self._handle_asset_build_progress(name, message)
            self._on_schema_project_progress(name, message)

        def on_done(result: object) -> None:
            self._finish_asset_build_progress(name, result)
            self._fetch_schema_after_project(name)
            self.toast(_i18n_t("toast.schema_initialized"))

        def on_fail(exc: object) -> None:
            self._fail_asset_build_progress(name, exc)
            self._projected.discard(name)

        self._run_background(
            "project_instance",
            {"name": name},
            on_done,
            on_error=on_fail,
            on_progress=on_progress,
        )

    def _on_schema_project_progress(self, name: str, message: object) -> None:
        if name != self.current_connection():
            return
        projecting = _i18n_t("schema.projecting")
        if self.schema_rows:
            label = self._progress_label(message) if message else ""
            text = f"{projecting} · {label}" if label else projecting
            self._ensure_ui_state().statusbar_message(text)
            return
        # Build progress card owns the visible loading state — no duplicate tree row.

    def _handle_asset_build_progress(self, conn: str, message: object) -> None:
        if conn == self.current_connection():
            self._ensure_ui_state().schema_build_progress_update(message)
            self._schedule_live_schema_refresh(conn, message)
        label = self._progress_label(message)
        if label:
            self._ensure_ui_state().statusbar_message(label)
            self._append_active_build_log(label)

    def _finish_asset_build_progress(self, conn: str, result: object) -> None:
        if conn != self.current_connection():
            return
        stats = result.get("stats", {}) if isinstance(result, dict) else {}
        tables = int((stats or {}).get("tables") or 0) if isinstance(stats, dict) else 0
        columns = int((stats or {}).get("columns") or 0) if isinstance(stats, dict) else 0
        message = _i18n_t(
            "build.progress_done_summary",
            tables=tables,
            columns=columns,
            queries=(stats or {}).get("total_queries", 0) if isinstance(stats, dict) else 0,
            errors=len((stats or {}).get("errors") or []) if isinstance(stats, dict) else 0,
        )
        self._ensure_ui_state().schema_build_progress_finish(message)
        self._append_active_build_log(message)

    def _fail_asset_build_progress(self, conn: str, exc: object) -> None:
        if conn == self.current_connection():
            self._ensure_ui_state().schema_build_progress_finish(str(exc), failed=True)
            self._fetch_schema_after_project(conn)

    def _schedule_live_schema_refresh(self, conn: str, message: object) -> None:
        if conn != self.current_connection() or not isinstance(message, dict):
            return
        node_id = str(message.get("node_id") or "")
        if str(message.get("status") or "") != "completed" or not node_id.startswith("build:db:"):
            return
        running: set[str] = getattr(self, "_schema_live_refresh_running", set())
        pending: set[str] = getattr(self, "_schema_live_refresh_pending", set())
        self._schema_live_refresh_running = running
        self._schema_live_refresh_pending = pending
        if conn in running:
            pending.add(conn)
            return
        running.add(conn)

        def start_refresh() -> None:
            if conn != self.current_connection():
                running.discard(conn)
                pending.discard(conn)
                return
            self.tasks.start(
                "schema_tree",
                {"name": conn},
                on_done=lambda rows: self._on_live_schema_refresh_done(conn, rows),
                on_failed=lambda _exc: self._on_live_schema_refresh_done(conn, None),
            )

        QTimer.singleShot(180, start_refresh)

    def _on_live_schema_refresh_done(self, conn: str, rows: object) -> None:
        running: set[str] = getattr(self, "_schema_live_refresh_running", set())
        pending: set[str] = getattr(self, "_schema_live_refresh_pending", set())
        running.discard(conn)
        if conn == self.current_connection() and isinstance(rows, list):
            self.schema_rows = rows
            self._ensure_ui_state().schema_loaded(self.schema_rows, self._schema_completion())
        if conn in pending:
            pending.discard(conn)
            self._schedule_live_schema_refresh(conn, {"node_id": "build:db:pending", "status": "completed"})

    def _append_active_build_log(self, text: str) -> None:
        key = self.ask_tab.active_key()
        if key and self.ask_tab.turn_open(key):
            self.ask_tab.append_activity(key, text)

    @staticmethod
    def _progress_label(message: object) -> str:
        from dbaide.agent.progress_events import progress_label
        return progress_label(message if isinstance(message, dict) else str(message or ""))

    def _project_failed(self, name: str, message: str) -> None:
        self._projected.discard(name)  # allow a retry on the next select (e.g. DB was down)
        # Refresh from store — partial/base docs and instance_doc.errors stay visible.
        if name == self.current_connection():
            self._fetch_schema_after_project(name)

    def _fetch_schema_after_project(self, name: str) -> None:
        if name != self.current_connection():
            return
        for conn in self.bootstrap.get("connections") or []:
            if conn.get("name") == name:
                conn["asset_status"] = "ready"
                break
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
        from dbaide.i18n import t as _i18n_t
        msg = _i18n_t("schema.load_failed", error=message)
        self.toast(msg)
        self.conversation_controller.sync_work_ui()
        self._ensure_ui_state().statusbar_message(msg)

    def refresh_joins(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.joins.load([])
            return

        def on_loaded(result: dict[str, Any]) -> None:
            self.joins.load(result.get("joins") or [])

        self._run_background("list_joins", {"connection_name": conn}, on_loaded)

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
        payload = {**payload, "connection_name": conn, "source": "user"}

        def on_done(_result: object) -> None:
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_saved"))
            self.refresh_joins()

        self._run_background("add_join", payload, on_done)

    def _update_join(self, payload: dict[str, Any]) -> None:
        conn = self.current_connection()
        if not conn:
            return

        def on_done(_result: object) -> None:
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_updated"))
            self.refresh_joins()

        self._run_background("update_join", {**payload, "connection_name": conn}, on_done)

    def _delete_join(self, join_id: str) -> None:
        conn = self.current_connection()
        if not conn:
            return

        def on_done(_result: object) -> None:
            self.bus.emit(JOINS_CHANGED, {"instance": conn})
            self.toast(_i18n_t("toast.join_deleted"))
            self.refresh_joins()

        self._run_background("delete_join", {"connection_name": conn, "id": join_id}, on_done)

    def open_sql(self, sql: str) -> None:
        self.tabbar.setCurrentIndex(1)
        self.workbench.open_sql(sql)

    def submit_composer(self, question: str) -> None:
        key = self.run_state.active_key
        # Active slot is awaiting a clarification reply → route there.
        if key and self.run_state.pending_resume_for(key):
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
            self.run_state.activate(key)
            self.current_session_id = ""
            self.ask_tab.set_active(key)
        self._last_question = question
        self.run_state.set_question(key, question)
        # Database scope for the agent comes from composer attachments (schema_scope),
        # not a global selector — payload database is left empty for auto scope.
        attachments = self.composer.attachments()
        schema_scope = self._build_attached_scope(attachments) if attachments else {}
        self.composer.clear_attachments()
        self.composer.clear_input()
        self.ask_tab.append_user(key, question, connection=conn, database="", attachments=attachments)
        # Fresh trace for this turn (streamed inline into the turn's status chip).
        self.run_state.set_trace(key, [])
        self.conversation_controller.start_ask(key, {
            "connection_name": conn,
            "question": question,
            "database": "",
            "session_id": self.run_state.session_for(key),
            "schema_scope": schema_scope,
            "attachments": attachments,  # raw UI chips — persisted on the turn
        })

    def _submit_clarification(self, key: str, reply: str) -> None:
        reply = str(reply or "").strip()
        if not reply:
            self.toast(_i18n_t("toast.enter_reply"))
            return
        resume_state = self.run_state.pending_resume_for(key)
        if not resume_state:
            # No pause for this slot — treat as a fresh question on the active slot.
            if key == self.run_state.active_key:
                self.submit_composer(reply)
            return
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        if self._assets_busy(conn):
            self.toast(_i18n_t("toast.assets_busy"))
            return
        original_question = str(resume_state.get("question") or self.run_state.question_for(key))
        # Consume the pause: controller queueing guarantees the reply is never
        # lost even when every run slot is busy (it waits for a free slot).
        self.run_state.clear_pending_resume(key)
        if key == self.run_state.active_key:
            self.composer.clear_input()
        self.ask_tab.append_clarification_reply(key, reply)
        self.ask_tab.append_activity(key, f"User replied: {reply[:80]}")
        self.conversation_controller.start_ask(key, {
            "connection_name": conn,
            "question": original_question,
            "user_reply": reply,
            "resume_state": resume_state,
            "database": "",
            "session_id": self.run_state.session_for(key),
        })

    def build_assets(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return

        conns = {c["name"]: c for c in self.bootstrap.get("connections") or []}
        conn_cfg = self.service.cfg.get_connection(conn)
        default_workers = self.service.cfg.policy_for(conn_cfg).build_max_workers

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
        if self._assets_busy(conn):
            self.toast(_i18n_t("toast.task_running"))
            return
        payload: dict[str, Any] = {"name": conn}
        if databases:
            payload["databases"] = databases
        if options:
            payload.update(options)
        self._ensure_ui_state().schema_build_progress_start(_i18n_t("status.building"))

        def on_progress(message: object) -> None:
            self._handle_asset_build_progress(conn, message)

        def on_done(result: object) -> None:
            self._finish_asset_build_progress(conn, result)
            if conn == self.current_connection():
                self._fetch_schema_after_project(conn)
            stats = result.get("stats", {}) if isinstance(result, dict) else {}
            self.toast(
                _i18n_t("toast.assets_built")
                + _i18n_t(
                    "toast.build_stats",
                    queries=(stats or {}).get("total_queries", 0),
                    peak=(stats or {}).get("peak_inflight", 0),
                )
            )

        def on_fail(exc: object) -> None:
            self._fail_asset_build_progress(conn, exc)
            self._background_failed(exc)

        self._run_background(
            "build_assets",
            payload,
            on_done,
            on_error=on_fail,
            on_progress=on_progress,
        )

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
            config_dir=str(self.service.cfg.path.parent),
            parent=self,
            initial_page=page,
        )
        dialog.connection_saved.connect(lambda payload: self._settings_save_connection(dialog, payload))
        dialog.connection_deleted.connect(lambda name: self._settings_delete_connection(dialog, name))
        dialog.excel_collection_changed.connect(lambda name: self._settings_excel_changed(name))
        dialog.connection_test.connect(lambda payload: self._settings_test_connection(dialog, payload))
        dialog.model_saved.connect(lambda payload: self._settings_save_model(dialog, payload))
        dialog.model_deleted.connect(lambda name: self._settings_delete_model(dialog, name))
        dialog.model_test.connect(lambda payload: self._settings_test_model(dialog, payload))
        dialog.resource_saved.connect(self._settings_save_resources)
        dialog.language_changed.connect(self._change_language)
        dialog.theme_changed.connect(self._change_theme)
        dialog.stream_answers_changed.connect(self._change_stream_answers)
        dialog.debug_trace_changed.connect(self._change_debug_trace)
        dialog.export_connection.connect(lambda name: self._settings_export_connection(dialog, name))
        dialog.import_requested.connect(lambda path: self._settings_import_connection(dialog, path))
        dialog.export_all_requested.connect(lambda: self._settings_export_all(dialog))
        self._settings_dialog = dialog
        cached = getattr(self, "_release_check", None)
        if cached is not None:
            from dbaide.app_info import app_version
            from dbaide.release_check import ReleaseCheckResult

            if isinstance(cached, ReleaseCheckResult):
                latest = cached.latest
                dialog.set_release_check_result(
                    ok=cached.ok,
                    current_version=app_version(),
                    latest_version=latest.version if latest else "",
                    update_available=cached.update_available,
                    ahead_of_release=cached.ahead_of_release,
                    release_url=latest.html_url if latest else "",
                )
        elif not self._release_check_in_progress:
            self._start_release_check()
        dialog.exec()
        self._settings_dialog = None

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
            self.toast(_i18n_t("error.save_failed"))

    def _change_stream_answers(self, enabled: bool) -> None:
        # Persisted to config; the backend reads it per request (next query streams or
        # not). No UI state to push — the conversation just renders whatever arrives.
        try:
            self.service.cfg.set_stream_answers(bool(enabled))
        except Exception as exc:  # noqa: BLE001
            self.toast(_i18n_t("error.save_failed"))

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
            saved = dict(payload)
            saved["has_password"] = bool(payload.get("password") or
                                         dialog._connections.get(payload["name"], {}).get("has_password"))
            dialog._connections[payload["name"]] = saved
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
        """Build the optional enrichment (LLM summary + sample rows) for a table
        or whole database, from the schema-tree context menu."""
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        if self._assets_busy(conn):
            self.toast(_i18n_t("toast.task_running"))
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

        progress_title = _i18n_t("status.enriching")
        self._ensure_ui_state().schema_build_progress_start(progress_title)

        def on_progress(message: object) -> None:
            self._handle_asset_build_progress(conn, message)

        def on_done(result: object) -> None:
            self._finish_asset_build_progress(conn, result)
            if conn != self.current_connection():
                return
            self._fetch_schema_after_project(conn)
            self.toast(_i18n_t("toast.enriched", target=target))
            self.bus.emit(ASSETS_CHANGED, {"instance": conn})

        def on_fail(exc: object) -> None:
            self._fail_asset_build_progress(conn, exc)
            if conn != self.current_connection():
                return
            self.toast(_i18n_t("toast.enrich_failed", error=str(exc)))

        self._run_background(action, payload, on_done, on_error=on_fail, on_progress=on_progress)

    def _backup_node(self, node: dict[str, Any]) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast(_i18n_t("toast.select_connection"))
            return
        kind = str(node.get("kind") or "")
        _instance, database, table, _column = self._schema_path_parts(node)
        if kind == "table" and table:
            scope = "table"
        elif kind == "database" and database:
            scope = "database"
            table = ""
        else:
            return
        from dbaide.desktop.dialogs.backup import BackupDialog
        dlg = BackupDialog(self.service, conn, database, table, scope=scope, parent=self)
        dlg.exec()
        self._ensure_backup_manager_refreshed()

    def _ensure_backup_manager_refreshed(self) -> None:
        tabs = self.workbench.tabs
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if w is not None and type(w).__name__ == "BackupManager":
                w.refresh()

    def open_backup_manager(self) -> None:
        self.tabbar.setCurrentIndex(1)
        tabs = self.workbench.tabs
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if w is not None and type(w).__name__ == "BackupManager":
                tabs.setCurrentIndex(i)
                return
        from dbaide.desktop.dialogs.backup import BackupManager
        mgr = BackupManager(service=self.service)
        idx = tabs.addTab(mgr, _i18n_t("backup.manager"))
        tabs.setCurrentIndex(idx)

    def _settings_delete_connection(self, dialog: SettingsDialog, name: str) -> None:
        entry = next((c for c in (self.bootstrap.get("connections") or []) if c.get("name") == name), {})
        db_path = str(entry.get("path") or "")

        def on_done(_result: object) -> None:
            self._cleanup_excel_collection(db_path)
            if not sip.isdeleted(dialog):
                dialog.remove_connection_entry(name)
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": name})
            self.toast(_i18n_t("toast.conn_removed"))

        self._run_background("delete_connection", {"name": name}, on_done)

    def _cleanup_excel_collection(self, db_path: str) -> None:
        """If the deleted connection was an Excel collection, remove its generated files."""
        from dbaide.ingest import collection_for_connection
        collection = collection_for_connection(self.service.cfg.path.parent, db_path)
        if collection is None:
            return
        import shutil
        shutil.rmtree(collection.dir, ignore_errors=True)

    def _settings_excel_changed(self, name: str) -> None:
        """A workbook was added/removed/renamed: the collection's tables changed. Excel data
        is tiny and local, so we just rebuild the base catalog projection (cheap, no LLM)
        rather than a diff-based sync — fast and always correct."""
        def on_done(_result: object) -> None:
            self.bus.emit(CONNECTIONS_CHANGED, {"instance": name})

        self._run_background("project_instance", {"name": name}, on_done, on_error=lambda _e: None)

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
            saved = dict(payload)
            saved["has_api_key"] = bool(payload.get("api_key") or
                                        dialog._models.get(payload["name"], {}).get("has_api_key"))
            dialog._models[payload["name"]] = saved
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
        def on_done(_result: object) -> None:
            if not sip.isdeleted(dialog):
                dialog.remove_model_entry(name)
            self.bus.emit(MODELS_CHANGED, {"model": name})
            self.toast(_i18n_t("toast.model_removed"))

        self._run_background("delete_model", {"name": name}, on_done)

    def _settings_test_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        self._ensure_ui_state().set_settings_busy(dialog, "test", True, target="model")

        def on_done(result: dict[str, Any]) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="model")
            dialog.show_test_result(bool(result.get("ok")), str(result.get("message") or "OK"), target="model")

        def on_fail(exc: object) -> None:
            self._ensure_ui_state().set_settings_busy(dialog, "test", False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("test_model_profile", payload, on_done, on_error=on_fail)

    # ── import / export ─────────────────────────────────────────────────────--

    def _settings_export_connection(self, dialog: SettingsDialog, name: str) -> None:
        default_name = f"dbaide-{name}.json"
        path, _ = get_save_file_name(
            dialog, _i18n_t("settings.export_conn"), default_name,
            _i18n_t("import.file_filter"),
        )
        if not path:
            return

        def on_done(result: dict[str, Any]) -> None:
            import json
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
                self.toast(_i18n_t("toast.export_ok", path=path))
            except OSError as exc:
                self.toast(_i18n_t("error.export_failed", error=str(exc)))

        self._run_background("export_connection", {"connection_name": name}, on_done)

    def _settings_import_connection(self, dialog: SettingsDialog, path: str) -> None:
        import json
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self.toast(_i18n_t("error.import_failed", error=str(exc)))
            return

        export_meta = data.get("dbaide_export") or {}
        export_type = export_meta.get("type", "")
        if export_type not in ("connection", "full"):
            self.toast(_i18n_t("error.import_failed", error="Not a valid DBAide export file"))
            return

        from dbaide.desktop.dialogs.message_dialog import confirm as dialog_confirm
        if export_type == "connection":
            conn_name = str((data.get("connection") or {}).get("name") or "")
            existing_names = {c.get("name") for c in (self.bootstrap.get("connections") or [])}
            if conn_name and conn_name in existing_names:
                if not dialog_confirm(dialog, _i18n_t("import.confirm_title"),
                                      _i18n_t("import.confirm_overwrite", name=conn_name)):
                    return
        elif export_type == "full":
            n_conn = len(data.get("connections") or [])
            n_model = len(data.get("models") or [])
            if not dialog_confirm(dialog, _i18n_t("import.confirm_title"),
                                  _i18n_t("import.confirm_overwrite_full", n=n_conn, m=n_model)):
                return

        def on_done(result: dict[str, Any]) -> None:
            if export_type == "connection":
                name = str(result.get("name") or "")
                self.toast(_i18n_t("toast.import_ok", name=name))
                if not sip.isdeleted(dialog):
                    # Reload connection data.
                    self._reload_after_import(dialog)
            else:
                nc = int(result.get("connections") or 0)
                nm = int(result.get("models") or 0)
                self.toast(_i18n_t("toast.import_all_ok", n=nc, m=nm))
                if not sip.isdeleted(dialog):
                    self._reload_after_import(dialog)

        def on_error(exc: object) -> None:
            self.toast(_i18n_t("error.import_failed", error=str(exc)))

        self._run_background("import_connection", {"data": data}, on_done, on_error=on_error)

    def _settings_export_all(self, dialog: SettingsDialog) -> None:
        path, _ = get_save_file_name(
            dialog, _i18n_t("settings.export_all"), "dbaide-config.json",
            _i18n_t("import.file_filter"),
        )
        if not path:
            return

        def on_done(result: dict[str, Any]) -> None:
            import json
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
                self.toast(_i18n_t("toast.export_ok", path=path))
            except OSError as exc:
                self.toast(_i18n_t("error.export_failed", error=str(exc)))

        self._run_background("export_all", {}, on_done)

    def _reload_after_import(self, dialog: SettingsDialog) -> None:
        """Refresh the settings dialog and main UI after an import."""
        def on_loaded(result: dict[str, Any]) -> None:
            self._on_bootstrap_loaded(result)
            if not sip.isdeleted(dialog):
                dialog._connections = {c["name"]: dict(c) for c in (result.get("connections") or [])}
                dialog._models = {m["name"]: dict(m) for m in (result.get("models") or [])}
                dialog._default_connection = str(result.get("default_connection") or "")
                dialog._default_model = str(result.get("default_model") or "default")
                dialog._reload_connection_list()
                dialog._reload_model_list()

        self._run_background("bootstrap", {}, on_loaded)

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

    def _export_all_from(self, doc, payload: dict[str, Any]) -> None:
        """Export all rows (no LIMIT) for the current table/filter and save to file."""
        fmt = str(payload.pop("format", "csv"))

        def on_loaded(result: dict[str, Any]) -> None:
            rows = result.get("rows") or []
            columns = result.get("columns") or []
            if not rows:
                self.toast(_i18n_t("data.no_rows"))
                return
            from dbaide.rendering.table import export_csv, export_json
            table_name = str(payload.get("table") or "table")
            if fmt == "json":
                content = export_json(rows, columns)
                ext, filt = "json", "JSON (*.json)"
            else:
                content = export_csv(rows, columns)
                ext, filt = "csv", "CSV (*.csv)"
            path, _ = get_save_file_name(
                self, _i18n_t("result.export_title"), f"{table_name}.{ext}", filt,
            )
            if path:
                try:
                    with open(path, "w", encoding="utf-8", newline="") as fh:
                        fh.write(content)
                    msg = _i18n_t("result.export_title") + f" → {path}"
                    # Don't let a row-cap pass silently — the user asked for "all rows".
                    if result.get("capped"):
                        msg += " · " + _i18n_t("result.export_capped", n=f"{len(rows):,}")
                    self.toast(msg)
                except OSError as exc:
                    self.toast(str(exc))

        self._run_background("export_table_all", payload, on_loaded)

    def _load_table_doc(self, path: str) -> None:
        """Load asset markdown for a TableDocument's doc sub-tab."""
        if not path:
            return

        def on_loaded(res: dict[str, Any]) -> None:
            parts = path.split(".")
            if len(parts) >= 3:
                conn, db, table = parts[0], parts[1], ".".join(parts[2:])
                self.workbench.update_table_doc(conn, db, table, res.get("markdown") or "")

        self._run_background("asset_markdown", {"path": path}, on_loaded)

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
        kind = str(data.get("kind") or "")
        if kind == "table":
            _instance, database, table, _column = self._schema_path_parts(data)
            if database and table:
                conn = self.current_connection()
                self.tabbar.setCurrentIndex(1)
                if self.workbench.focus_table_doc(conn, database, table):
                    return
                self.workbench.open_table(
                    conn, database, table, data.get("children") or [],
                    relations={
                        "foreign_keys": data.get("foreign_keys") or [],
                        "referenced_by": data.get("referenced_by") or [],
                    },
                    indexes=data.get("indexes") or [],
                    dialect=self._dialect(),
                )
                doc = self.workbench.tabs.currentWidget()
                if hasattr(doc, "focus_doc"):
                    doc.focus_doc()
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
        qualified = ".".join(p for p in (database, table, column) if p) or conn
        label = f"{_i18n_t('notes.scope_' + kind)} · {qualified}"

        def on_loaded(res: dict[str, Any]) -> None:
            records = res.get("annotations") or []
            current = str(records[0].get("note")) if records else ""
            self._show_note_dialog(conn, kind, database, table, body, label, current)

        def on_load_failed(_exc: object) -> None:
            # Still let the user create a note even if loading existing failed.
            self._show_note_dialog(conn, kind, database, table, body, label, "")

        self._run_background("list_annotations", body, on_loaded, on_error=on_load_failed)

    def _show_note_dialog(
        self, conn: str, kind: str, database: str, table: str,
        body: dict[str, Any], label: str, current: str,
    ) -> None:
        dialog = NoteEditorDialog(self, target_label=label, note=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.value()
        doc_path = f"{conn}.{database}" if kind == "database" else f"{conn}.{database}.{table}"

        if text:
            def on_saved(_r: object) -> None:
                self.toast(_i18n_t("toast.note_saved"))
                self._refresh_doc_if_open(doc_path)
            self._run_background("add_annotation", {**body, "note": text}, on_saved)
        else:
            def on_deleted(_r: object) -> None:
                self.toast(_i18n_t("toast.note_deleted"))
                self._refresh_doc_if_open(doc_path)
            self._run_background("delete_annotation", body, on_deleted)

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
                    dialect=self._dialect(),
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
        where = _fk_filter_where(ref_column, value, self._dialect())
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
        self.run_state.activate(key)
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
            self.run_state.set_session(sid, sid)
            self.run_state.set_trace(sid, (turns[-1].get("trace") if turns else []) or [])
            self._activate_slot(sid)
            self.switch_tab("Chat")

        self._run_background("load_session", {"connection_name": conn, "session_id": session_id}, on_loaded)

    def _activate_slot(self, key: str) -> None:
        """Bring slot ``key`` to the front: show its conversation and sync the
        composer to whether it is idle / running / awaiting a reply. (Each turn's
        trace travels inline with the conversation, so there's nothing else to swap.)"""
        self.run_state.activate(key)
        self.current_session_id = self.run_state.session_for(key) or (key if not key.startswith("new:") else "")
        self.ask_tab.set_has_connection(bool(self.current_connection()))
        self.ask_tab.set_active(key)
        self.conversation_controller.sync_work_ui()

    def rename_session(self, session_id: str, title: str) -> None:
        conn = self.current_connection()
        if not conn or not session_id:
            return

        def on_done(_result: object) -> None:
            self._load_sessions(conn)

        def on_error(_exc: object) -> None:
            self.toast(_i18n_t("error.rename_failed"))

        self._run_background(
            "rename_session",
            {"connection_name": conn, "session_id": session_id, "title": title},
            on_done, on_error=on_error,
        )

    def delete_session(self, session_id: str) -> None:
        conn = self.current_connection()
        if not conn or not session_id:
            return

        def on_done(_result: object) -> None:
            # Drop the slot for the deleted session (cancel its run if any) — one call
            # keeps run-state and the ask-tab view in lockstep.
            if self.ask_tab.has_slot(session_id):
                was_active = session_id == self.run_state.active_key
                self.conversation_controller.discard_slot(session_id)
                if was_active:
                    self.current_session_id = ""
                    self.ask_tab.set_has_connection(bool(conn))
                    self.conversation_controller.sync_work_ui()
            self._load_sessions(conn)

        def on_error(_exc: object) -> None:
            self.toast(_i18n_t("error.delete_failed"))

        self._run_background(
            "delete_session",
            {"connection_name": conn, "session_id": session_id},
            on_done, on_error=on_error,
        )

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

        context = self.run_state.active_debug_context(
            connection_name=self.current_connection(),
            session_id=self.current_session_id,
        )
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
        from dbaide.llm_errors import format_user_error
        friendly = format_user_error(exc)
        msg = _i18n_t("error.turn.error", message=friendly)
        key = self.conversation_controller.active_or_new_key()
        if self.ask_tab.turn_open(key):
            self.ask_tab.finish_turn_error(key, msg)
        else:
            self.ask_tab.append_note(key, _i18n_t("note.error"), msg)
        if modal:
            dialog_warn(self, "DBAide", friendly)
        else:
            self.toast(friendly)


class DBAideDesktop:
    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def run(self) -> None:
        app = QApplication.instance() or QApplication(sys.argv)
        from dbaide.desktop.platform_ui import configure_application
        configure_application(app)
        app.setApplicationName("DBAide")
        icon = _app_icon()
        if icon is not None:
            app.setWindowIcon(icon)
        # Fusion makes global QSS apply consistently on macOS (native style ignores many label rules).
        app.setStyle("Fusion")
        window = MainWindow(self.service)
        if icon is not None:
            window.setWindowIcon(icon)
        window.show()
        app.exec()


def _app_icon() -> "QIcon | None":
    """Window icon uses the original DBAide brand icon."""
    from dbaide.desktop.components.icons import app_icon
    icon = app_icon()
    return icon if not icon.isNull() else None
