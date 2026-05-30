from __future__ import annotations

import json
import sys
from typing import Any, Callable

from PyQt6.QtCore import Qt, QSettings, QThreadPool
from PyQt6.QtWidgets import (
    QApplication,
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
from dbaide.desktop.dialogs.settings import SettingsDialog
from dbaide.agent.progress_events import progress_label
from dbaide.desktop.theme import APP_STYLE
from dbaide.desktop.views.ask_tab import AskTab
from dbaide.desktop.views.assets_tab import AssetsTab
from dbaide.desktop.views.history_tab import HistoryTab
from dbaide.desktop.views.right_panel import RightPanel
from dbaide.desktop.views.sidebar import Sidebar
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.topbar import TopBar
from dbaide.desktop.workers import CancelledError, ServiceWorker


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopService) -> None:
        super().__init__()
        self.service = service
        self.pool = QThreadPool.globalInstance()
        self.bootstrap: dict[str, Any] = {}
        self.schema_rows: list[dict[str, Any]] = []
        self.running = False
        self._last_question = ""
        self._last_action = ""
        self._pending_resume: dict[str, Any] | None = None
        self._current_worker: ServiceWorker | None = None
        self._settings = QSettings("DBAide", "DBAide")
        self._tab_names = ("Ask", "SQL", "Assets", "History")
        self.setWindowTitle("DBAide")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(APP_STYLE)
        self._build()
        self.refresh_all()

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
        layout.addWidget(self.topbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("mainSplitter")
        self.body_splitter = body
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.sidebar.schema_preview.connect(self.preview_schema)
        self.sidebar.schema_selected.connect(self.open_schema_asset)
        self.sidebar.settings_requested.connect(lambda: self.open_settings("connections"))

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(12, 12, 12, 8)
        center_layout.setSpacing(8)
        tab_row = QHBoxLayout()
        self.tabbar = QTabBar()
        self.tabbar.setProperty("segmented", True)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setExpanding(False)
        for name in self._tab_names:
            self.tabbar.addTab(name)
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        tab_row.addWidget(self.tabbar)
        tab_row.addStretch(1)
        center_layout.addLayout(tab_row)

        self.stack = QStackedWidget()
        self.ask_tab = AskTab()
        self.sql_tab = SqlTab()
        self.assets_tab = AssetsTab()
        self.history_tab = HistoryTab()
        self.ask_tab.empty_action.connect(self._empty_action)
        self.ask_tab.open_sql.connect(self.open_sql)
        self.ask_tab.clarification_choice.connect(self._submit_clarification)
        self.sql_tab.validate_requested.connect(self.validate_sql)
        self.sql_tab.explain_requested.connect(self.explain_sql)
        self.sql_tab.run_requested.connect(lambda sql, _action: self.execute_sql(sql))
        self.assets_tab.asset_selected.connect(self.load_asset)
        self.assets_tab.search_requested.connect(self.search_assets)
        self.history_tab.history_selected.connect(self.load_history)
        self.history_tab.history_preview.connect(self.preview_history)
        self.stack.addWidget(self.ask_tab)
        self.stack.addWidget(self.sql_tab)
        self.stack.addWidget(self.assets_tab)
        self.stack.addWidget(self.history_tab)
        center_layout.addWidget(self.stack, 1)

        self.composer = ComposerWidget()
        self.composer.submit_requested.connect(self.submit_composer)
        self.composer.stop_requested.connect(self.stop_task)
        self.composer.model_changed.connect(self._model_changed)
        center_layout.addWidget(self.composer)

        self.right = RightPanel()
        self.right.copy_trace_requested.connect(self.copy_trace)
        self.right.clear_trace_requested.connect(self.right.clear_all)
        self.right.clear_conversation_requested.connect(self.ask_tab.clear_conversation)

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
            self.composer.set_placeholder("Add or select a connection to start")

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

    def switch_tab(self, name: str) -> None:
        if name in self._tab_names:
            self.tabbar.setCurrentIndex(self._tab_names.index(name))

    def _connection_changed(self, _text: str) -> None:
        conn = self.current_connection()
        if conn:
            self._refresh_connection_context(conn)

    def _database_changed(self, _text: str) -> None:
        database = self.current_database()
        self.toast(f"Database scope: {database or 'auto'}")

    def _refresh_connection_context(self, conn_name: str) -> None:
        conns = self.bootstrap.get("connections") or []
        self._load_schema(conn_name)
        self._load_history(conn_name)
        asset_status = "missing"
        for c in conns:
            if c["name"] == conn_name:
                asset_status = c.get("asset_status") or "missing"
                break
        self.topbar.set_asset_status(asset_status)
        hint = "  Enter 换行 · ⌘Enter 发送"
        self.composer.set_placeholder(
            (
                "Ask about your data, e.g. \"最近 7 天每天订单数\""
                if asset_status == "ready"
                else "Ask a question, or build assets for better accuracy"
            )
            + hint
        )

    def _load_schema(self, name: str) -> None:
        self._run_background(
            "schema_tree",
            {"name": name},
            lambda rows: self._apply_schema_loaded(name, rows),
            on_error=lambda exc: self._apply_schema_error(str(exc)),
        )

    def _apply_schema_loaded(self, name: str, rows: list[dict[str, Any]]) -> None:
        if name != self.current_connection():
            return
        self.schema_rows = rows
        dbs = [row["name"] for row in self.schema_rows]
        self.topbar.set_databases(dbs)
        self.sidebar.load_schema(self.schema_rows)
        self.assets_tab.load_schema(self.schema_rows)

    def _apply_schema_error(self, message: str) -> None:
        self.schema_rows = []
        self.sidebar.load_schema([], error=message)
        self.topbar.set_databases([])
        self.assets_tab.load_schema([])
        self.toast(f"Schema load failed: {message}")

    def _load_history(self, name: str) -> None:
        self._run_background(
            "list_history",
            {"connection_name": name},
            lambda entries: self.history_tab.load(entries if name == self.current_connection() else entries),
        )

    def open_sql(self, sql: str) -> None:
        self.sql_tab.set_sql(sql)
        self.switch_tab("SQL")

    def submit_composer(self, question: str, policy: str) -> None:
        if self._pending_resume:
            self._submit_clarification(question)
            return
        if not question:
            self.toast("Enter a question first")
            return
        conn = self.current_connection()
        if not conn:
            self.toast("Select a connection first")
            return
        self._last_question = question
        database = self.current_database()
        self.composer.clear_input()
        self.ask_tab.append_user(question, connection=conn, database=database, policy=policy)
        self.right.trace.begin_live()
        self.right.tabs.setCurrentWidget(self.right.trace)
        self.run_action("ask", {
            "connection_name": conn,
            "question": question,
            "database": database,
            "execution_policy": policy,
            "show_trace": True,
        })

    def _submit_clarification(self, reply: str) -> None:
        reply = str(reply or "").strip()
        if not reply:
            self.toast("Enter a reply first")
            return
        if not self._pending_resume:
            self.submit_composer(reply, self.composer.policy())
            return
        conn = self.current_connection()
        if not conn:
            self.toast("Select a connection first")
            return
        database = self.current_database()
        policy = self.composer.policy()
        original_question = str(self._pending_resume.get("question") or self._last_question)
        resume_state = self._pending_resume
        self._pending_resume = None
        self.composer.clear_input()
        self.ask_tab.append_clarification_reply(reply)
        self.ask_tab.append_activity(f"User replied: {reply[:80]}")
        self.right.trace.begin_live()
        self.right.tabs.setCurrentWidget(self.right.trace)
        self.run_action("ask", {
            "connection_name": conn,
            "question": original_question,
            "user_reply": reply,
            "resume_state": resume_state,
            "database": database,
            "execution_policy": policy,
            "show_trace": True,
        })
        self._restore_composer_placeholder()

    def _restore_composer_placeholder(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.composer.set_placeholder("Add or select a connection to start")
            return
        conns = self.bootstrap.get("connections") or []
        asset_status = "missing"
        for c in conns:
            if c["name"] == conn:
                asset_status = c.get("asset_status") or "missing"
                break
        hint = "  Enter 换行 · ⌘Enter 发送"
        self.composer.set_placeholder(
            (
                "Ask about your data, e.g. \"最近 7 天每天订单数\""
                if asset_status == "ready"
                else "Ask a question, or build assets for better accuracy"
            )
            + hint
        )

    def build_assets(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast("Select a connection first")
            return
        self.topbar.set_asset_status("building")
        self.topbar.set_global_status("Building assets", "building")
        self.right.trace.begin_live()
        self.right.tabs.setCurrentWidget(self.right.trace)
        self.run_action("build_assets", {"name": conn})

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
        dialog = SettingsDialog(
            connections=self.bootstrap.get("connections") or [],
            models=self.bootstrap.get("models") or [],
            default_connection=str(self.bootstrap.get("default_connection") or ""),
            default_model=str(self.bootstrap.get("default_model") or "default"),
            parent=self,
            initial_page=page,
        )
        dialog.connection_saved.connect(lambda payload: self._settings_save_connection(dialog, payload))
        dialog.connection_deleted.connect(self._settings_delete_connection)
        dialog.connection_test.connect(lambda payload: self._settings_test_connection(dialog, payload))
        dialog.model_saved.connect(lambda payload: self._settings_save_model(dialog, payload))
        dialog.model_deleted.connect(self._settings_delete_model)
        dialog.model_test.connect(lambda payload: self._settings_test_model(dialog, payload))
        dialog.exec()

    def _model_changed(self, model_name: str) -> None:
        if not model_name:
            return
        try:
            self.service.dispatch("set_default_model", {"name": model_name})
            self.refresh_all()
            active = self.bootstrap.get("model") or {}
            label = str(active.get("model") or model_name)
            self.toast(f"Model: {label}")
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
            self.toast("Connection saved")
            self.refresh_all()

        def on_fail(exc: object) -> None:
            dialog.set_save_busy(False, target="connection")
            dialog.show_test_result(False, str(exc), target="connection")

        self._run_background("save_connection", payload, on_done, on_error=on_fail)

    def _settings_delete_connection(self, name: str) -> None:
        try:
            self.service.dispatch("delete_connection", {"name": name})
            self.refresh_all()
            self.toast("Connection removed")
        except Exception as exc:
            self.fail(exc)

    def _settings_test_connection(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        dialog.set_test_busy(True, target="connection")

        def on_done(result: dict[str, Any]) -> None:
            dialog.set_test_busy(False, target="connection")
            dialog.show_test_result(True, str(result.get("message") or "Connection OK"), target="connection")

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
            self.toast("Model saved")
            self.refresh_all()

        def on_fail(exc: object) -> None:
            dialog.set_save_busy(False, target="model")
            dialog.show_test_result(False, str(exc), target="model")

        self._run_background("save_model", payload, on_done, on_error=on_fail)

    def _settings_delete_model(self, name: str) -> None:
        try:
            self.service.dispatch("delete_model", {"name": name})
            self.refresh_all()
            self.toast("Model removed")
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

    def validate_sql(self, sql: str) -> None:
        if not sql.strip():
            return
        self.run_action("validate_sql", {
            "connection_name": self.current_connection(),
            "database": self.current_database(),
            "sql": sql,
        })

    def explain_sql(self, sql: str) -> None:
        if not sql.strip():
            return
        self.run_action("explain_sql", {
            "connection_name": self.current_connection(),
            "database": self.current_database(),
            "sql": sql,
        })

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

    def preview_schema(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if path:
            self.run_action("preview_asset", {"path": path})

    def open_schema_asset(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if path:
            self.run_action("asset_markdown", {"path": path})

    def load_asset(self, path: str) -> None:
        if not path:
            return
        self.run_action("asset_markdown", {"path": path})
        self.switch_tab("Assets")

    def search_assets(self, query: str) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast("Select a connection first")
            return
        self._last_question = query
        self.run_action("search_assets", {
            "connection_name": conn,
            "query": query,
        })

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
            self.right.show_plan(entry)
            self.toast(f"Preview: {workflow_id}")
        except Exception as exc:
            self.toast(str(exc))

    def run_action(self, action: str, payload: dict[str, Any]) -> None:
        if self.running:
            self.toast("A task is already running")
            return
        self._last_action = action
        self.running = True
        self.composer.set_running(True)
        self.sql_tab.set_running(True)
        self.topbar.set_global_status("Running", "running")
        worker = ServiceWorker(self.service, action, payload)
        worker.signals.progress.connect(self.on_progress)
        worker.signals.done.connect(self.handle_result)
        worker.signals.failed.connect(self.handle_failure)
        self._current_worker = worker
        self.pool.start(worker)

    def stop_task(self) -> None:
        if self._current_worker and not self._current_worker.is_cancelled:
            self._current_worker.cancel()
            self.toast("Cancelling…")
            return
        self.running = False
        self.composer.set_running(False)
        self.sql_tab.set_running(False)
        self._restore_status_badge()

    def on_progress(self, message: object) -> None:
        label = progress_label(message if isinstance(message, dict) else str(message or ""))
        self.statusbar.showMessage(label)
        if not self.running:
            return
        if isinstance(message, dict):
            if self._last_action == "ask":
                self.ask_tab.append_activity_event(message)
            self.right.trace.append_live_event(message)
        else:
            text = str(message or "").strip()
            if text:
                if self._last_action == "ask":
                    self.ask_tab.append_activity(text)
                self.right.trace.append_live(text)

    def handle_result(self, action: str, result: Any) -> None:
        self._current_worker = None
        self.running = False
        self.composer.set_running(False)
        self.sql_tab.set_running(False)
        self._restore_status_badge()
        if action == "build_assets":
            self.right.trace.end_live()
            self.ask_tab.append_note(
                "Assets built",
                f"```json\n{json.dumps(result.get('stats', {}), ensure_ascii=False, indent=2)}\n```",
            )
            self.refresh_all()
            self.switch_tab("Ask")
            self.toast("Assets built")
            return
        if action == "ask":
            self.right.trace.end_live()
            if str(result.get("status") or "") == "wait_user":
                self._pending_resume = result.get("resume_state")
                self._last_question = str(result.get("question") or self._last_question)
                self.ask_tab.append_result(result)
                self.right.show_trace(result.get("trace") or [])
                self.composer.set_placeholder("Reply to continue…  Enter 换行 · ⌘Enter 发送")
                self.toast("Waiting for your reply")
                return
            self._pending_resume = None
            if str(result.get("status") or "") == "cancelled":
                if getattr(self.ask_tab, "_turn_open", False):
                    self.ask_tab.finish_turn_error("**Cancelled**: Task stopped by user.")
                self.toast("Cancelled")
                return
            self.ask_tab.append_result(result)
            self.right.show_trace(result.get("trace") or [])
            self.right.show_plan(result)
            self._load_history(self.current_connection())
            self._restore_composer_placeholder()
            return
        if action == "search_assets":
            self.assets_tab.show_search_hits(self._last_question, result)
            self.switch_tab("Assets")
            return
        if action == "preview_asset":
            self.right.show_inspector(
                markdown=result.get("markdown") or "",
                doc=result.get("doc"),
                focus=True,
            )
            return
        if action == "validate_sql":
            self.sql_tab.show_validation(result)
            return
        if action == "execute_sql":
            self.sql_tab.show_result(result)
            return
        if action == "explain_sql":
            self.sql_tab.show_explain(result)
            return
        if action == "asset_markdown":
            self.assets_tab.show_markdown(result.get("markdown") or "", title=result.get("path") or "Asset")
            self.right.show_inspector(
                markdown=result.get("markdown") or "",
                doc=result.get("doc"),
                focus=False,
            )
            return
        if action == "load_history":
            self.ask_tab.append_result(result)
            self.right.show_trace(result.get("trace") or [])
            self.right.show_plan(result)
            self.switch_tab("Ask")
            return
        if action == "test_connection":
            self.toast(str(result.get("message") or "Connection OK"))

    def handle_failure(self, exc: object) -> None:
        self._current_worker = None
        self.running = False
        self.composer.set_running(False)
        self.sql_tab.set_running(False)
        self.right.trace.end_live()
        if isinstance(exc, CancelledError):
            if getattr(self.ask_tab, "_turn_open", False):
                self.ask_tab.finish_turn_error("**Cancelled**: Task stopped by user.")
            self.toast("Cancelled")
            self._restore_status_badge()
            return
        self.fail(exc, modal=self._last_action not in ("ask", "preview_asset", "search_assets"))
        self._restore_status_badge()

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
        lines = []
        for i in range(self.right.trace.topLevelItemCount()):
            item = self.right.trace.topLevelItem(i)
            lines.append("\t".join(item.text(c) for c in range(3)))
        QApplication.clipboard().setText("\n".join(lines))

    def _empty_action(self, action_id: str) -> None:
        if action_id == "settings":
            self.open_settings("connections")
        elif action_id == "refresh":
            self.refresh_all()

    def toast(self, message: str) -> None:
        self.statusbar.showMessage(message, 4000)

    def fail(self, exc: object, *, modal: bool = True) -> None:
        msg = f"**{type(exc).__name__}**: {exc}"
        if getattr(self.ask_tab, "_turn_open", False):
            self.ask_tab.finish_turn_error(msg)
        elif self._last_action in ("ask", "preview_asset", "search_assets"):
            self.ask_tab.append_note("Error", msg)
        else:
            self.ask_tab.append_note("Error", msg)
        if self._last_action in ("validate_sql", "execute_sql", "explain_sql"):
            self.sql_tab.show_error(str(exc))
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
        window = MainWindow(self.service)
        window.show()
        app.exec()
