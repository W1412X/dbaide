from __future__ import annotations

import json
import sys
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool
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
from dbaide.desktop.service import DesktopService
from dbaide.desktop.theme import APP_STYLE
from dbaide.desktop.views.ask_tab import AskTab
from dbaide.desktop.views.assets_tab import AssetsTab
from dbaide.desktop.views.history_tab import HistoryTab
from dbaide.desktop.views.right_panel import RightPanel
from dbaide.desktop.views.sidebar import Sidebar
from dbaide.desktop.views.sql_tab import SqlTab
from dbaide.desktop.views.topbar import TopBar
from dbaide.desktop.workers import ServiceWorker


class MainWindow(QMainWindow):
    def __init__(self, service: DesktopService) -> None:
        super().__init__()
        self.service = service
        self.pool = QThreadPool.globalInstance()
        self.bootstrap: dict[str, Any] = {}
        self.schema_rows: list[dict[str, Any]] = []
        self.running = False
        self._last_question = ""
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
        self.topbar.refresh.connect(self.refresh_all)
        self.topbar.build_assets.connect(self.build_assets)
        self.topbar.settings.connect(lambda: self.open_settings("connections"))
        layout.addWidget(self.topbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.sidebar.schema_selected.connect(self.inspect_schema)
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
        self.sql_tab.validate_requested.connect(self.validate_sql)
        self.sql_tab.explain_requested.connect(self.explain_sql)
        self.sql_tab.run_requested.connect(lambda sql, _action: self.execute_sql(sql))
        self.assets_tab.asset_selected.connect(self.load_asset)
        self.history_tab.history_selected.connect(self.load_history)
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

        body.addWidget(self.sidebar)
        body.addWidget(center)
        body.addWidget(self.right)
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        body.setCollapsible(2, False)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([280, 780, 360])
        layout.addWidget(body, 1)

        self.setCentralWidget(root)
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Ready")

    def refresh_all(self) -> None:
        try:
            self.bootstrap = self.service.dispatch("bootstrap", {})
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
                self._load_schema(conn_name)
                self._load_history(conn_name)
                asset_status = "missing"
                for c in conns:
                    if c["name"] == conn_name:
                        asset_status = c.get("asset_status") or "missing"
                self.topbar.set_asset_status(asset_status)
                self.composer.set_placeholder(
                    "Ask about your data, e.g. \"最近 7 天每天订单数\""
                    if asset_status == "ready"
                    else "Ask a question, or build assets for better accuracy"
                )
            else:
                self.composer.set_placeholder("Add or select a connection to start")
            self.statusbar.showMessage("Ready")
        except Exception as exc:
            self.fail(exc)

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
        name = self.current_connection()
        if name:
            self._load_schema(name)
            self._load_history(name)

    def _load_schema(self, name: str) -> None:
        try:
            self.schema_rows = self.service.dispatch("schema_tree", {"name": name})
        except Exception:
            self.schema_rows = []
        dbs = [row["name"] for row in self.schema_rows]
        self.topbar.set_databases(dbs)
        self.sidebar.load_schema(self.schema_rows)
        self.assets_tab.load_schema(self.schema_rows)

    def _load_history(self, name: str) -> None:
        try:
            entries = self.service.dispatch("list_history", {"connection_name": name})
        except Exception:
            entries = []
        self.history_tab.load(entries)

    def open_sql(self, sql: str) -> None:
        self.sql_tab.set_sql(sql)
        self.switch_tab("SQL")

    def submit_composer(self, question: str, policy: str) -> None:
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

    def build_assets(self) -> None:
        conn = self.current_connection()
        if not conn:
            self.toast("Select a connection first")
            return
        self.topbar.set_asset_status("building")
        self.topbar.set_global_status("Building assets", "building")
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
        dialog.model_saved.connect(self._settings_save_model)
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
        try:
            self.service.dispatch("save_connection", payload)
            self.refresh_all()
            dialog._connections = {c["name"]: dict(c) for c in self.bootstrap.get("connections") or []}
            dialog._default_connection = str(self.bootstrap.get("default_connection") or "")
            dialog._reload_connection_list()
            self.toast("Connection saved")
        except Exception as exc:
            self.fail(exc)

    def _settings_delete_connection(self, name: str) -> None:
        try:
            self.service.dispatch("delete_connection", {"name": name})
            self.refresh_all()
            self.toast("Connection removed")
        except Exception as exc:
            self.fail(exc)

    def _settings_test_connection(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        try:
            result = self.service.dispatch("test_connection", payload)
            dialog.show_test_result(True, str(result.get("message") or "Connection OK"))
        except Exception as exc:
            dialog.show_test_result(False, str(exc))

    def _settings_save_model(self, payload: dict[str, Any]) -> None:
        try:
            self.service.dispatch("save_model", payload)
            self.refresh_all()
            self.toast("Model saved")
        except Exception as exc:
            self.fail(exc)

    def _settings_delete_model(self, name: str) -> None:
        try:
            self.service.dispatch("delete_model", {"name": name})
            self.refresh_all()
            self.toast("Model removed")
        except Exception as exc:
            self.fail(exc)

    def _settings_test_model(self, dialog: SettingsDialog, payload: dict[str, Any]) -> None:
        try:
            self.service.dispatch("save_model", payload)
            result = self.service.dispatch("test_model", {"name": payload.get("name")})
            dialog.show_test_result(bool(result.get("ok")), str(result.get("message") or "OK"))
        except Exception as exc:
            dialog.show_test_result(False, str(exc))

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
        path = str(data.get("path") or "")
        if path:
            self.load_asset(path)

    def load_asset(self, path: str) -> None:
        if path.startswith("search:"):
            query = path.split(":", 1)[1]
            self._last_question = query
            self.ask_tab.append_user(query, connection=self.current_connection(), database=self.current_database())
            self.run_action("ask", {
                "connection_name": self.current_connection(),
                "question": query,
                "database": self.current_database(),
                "execution_policy": self.composer.policy(),
                "show_trace": True,
            })
            self.switch_tab("Ask")
            return
        self.run_action("asset_markdown", {"path": path})
        self.switch_tab("Assets")

    def load_history(self, workflow_id: str) -> None:
        conn = self.current_connection()
        self.run_action("load_history", {"connection_name": conn, "workflow_id": workflow_id})

    def run_action(self, action: str, payload: dict[str, Any]) -> None:
        self.running = True
        self.composer.set_running(True)
        self.topbar.set_global_status("Running", "running")
        worker = ServiceWorker(self.service, action, payload)
        worker.signals.progress.connect(self.on_progress)
        worker.signals.done.connect(self.handle_result)
        worker.signals.failed.connect(self.handle_failure)
        self.pool.start(worker)

    def stop_task(self) -> None:
        self.toast("Cancelling is not yet supported for in-flight workflows")
        self.running = False
        self.composer.set_running(False)
        self._restore_status_badge()

    def on_progress(self, message: str) -> None:
        self.statusbar.showMessage(message)
        if self.running:
            self.ask_tab.append_activity(message)
            self.right.trace.append_live(message)

    def handle_result(self, action: str, result: Any) -> None:
        self.running = False
        self.composer.set_running(False)
        self._restore_status_badge()
        if action == "build_assets":
            self.ask_tab.append_note(
                "Assets built",
                f"```json\n{json.dumps(result.get('stats', {}), ensure_ascii=False, indent=2)}\n```",
            )
            self.refresh_all()
            self.toast("Assets built")
            return
        if action == "ask":
            self.right.trace.end_live()
            self.ask_tab.append_result(result)
            self.right.show_trace(result.get("trace") or [])
            self.right.show_plan(result)
            self._load_history(self.current_connection())
            return
        if action == "search_assets":
            self.ask_tab.append_search_hits(self._last_question, result)
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
            self.right.show_inspector(markdown=result.get("markdown") or "", doc=result.get("doc"))
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
        self.running = False
        self.composer.set_running(False)
        self.topbar.set_global_status("Failed", "failed")
        self.right.trace.end_live()
        self.fail(exc)

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

    def fail(self, exc: object) -> None:
        msg = f"**{type(exc).__name__}**: {exc}"
        if getattr(self.ask_tab, "_turn_open", False):
            self.ask_tab.finish_turn_error(msg)
        else:
            self.ask_tab.append_note("Error", msg)
        self.sql_tab.show_error(str(exc))
        QMessageBox.warning(self, "DBAide", str(exc))


class DBAideDesktop:
    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def run(self) -> None:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("DBAide")
        window = MainWindow(self.service)
        window.show()
        app.exec()
