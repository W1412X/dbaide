"""AI dashboard studio: an interactive HTML dashboard you build and refine by chat.

The builder agent (seeded with the conversation's analysis) authors the page +
recipes; it renders in a WebChannel-wired WebEngine view. A chat box at the
bottom sends refinement instructions ("make the month a date range", "add a pie
chart by category") back to the agent, which rewrites the page.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.dashboard_webview import DashboardWebView
from dbaide.desktop.components.inputs import STANDARD_FIELD_HEIGHT, configure_compact_field
from dbaide.desktop.dialogs.message_dialog import warn as dialog_warn
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t


class _BuildWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._payload = payload

    def run(self) -> None:
        try:
            self.done.emit(self._service.dispatch("build_dashboard_app", self._payload))
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ParametricDashboardStudio(QWidget):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._app_id = ""
        self._connection = ""
        self._worker: _BuildWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        self._title = QLabel(_t("app.window_title"))
        self._title.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        head.addWidget(self._title, 1)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{Theme.MUTED}; background:transparent;")
        head.addWidget(self._status)
        root.addLayout(head)

        self._web = DashboardWebView()
        root.addWidget(self._web, 1)

        refine = QHBoxLayout()
        refine.setSpacing(8)
        self._refine = QLineEdit()
        self._refine.setPlaceholderText(_t("app.refine_ph"))
        configure_compact_field(self._refine, height=STANDARD_FIELD_HEIGHT)
        self._refine.returnPressed.connect(self._on_refine)
        refine.addWidget(self._refine, 1)
        self._send = compact_button(_t("app.send"), primary=True, width=88)
        self._send.clicked.connect(self._on_refine)
        refine.addWidget(self._send)
        root.addLayout(refine)

    # -- public ---------------------------------------------------------------

    def start(self, *, name: str, connection_name: str, context: list[dict], instruction: str) -> None:
        self._connection = connection_name
        self._build({"name": name, "connection_name": connection_name,
                     "context": context, "instruction": instruction})

    def open_existing(self, app_id: str) -> None:
        data = self._service.dispatch("get_dashboard_app", {"id": app_id})
        app = data.get("app") or {}
        self._app_id = str(app.get("id") or "")
        self._connection = str(app.get("connection_name") or "")
        self._title.setText(str(app.get("name") or _t("app.window_title")))
        self._render(str(app.get("html") or ""))

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.wait()
            self._worker.deleteLater()
            self._worker = None

    def closeEvent(self, e) -> None:  # noqa: N802
        self.shutdown()   # don't let a build QThread be destroyed mid-run on close
        super().closeEvent(e)

    # -- build / refine -------------------------------------------------------

    def _build(self, payload: dict) -> None:
        if self._worker is not None:
            return
        self._set_busy(True)
        worker = _BuildWorker(self._service, payload, self)
        worker.done.connect(self._on_built)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_refine(self) -> None:
        text = self._refine.text().strip()
        if not text or self._worker is not None or not self._app_id:
            return
        self._refine.clear()
        self._build({"app_id": self._app_id, "instruction": text})

    def _on_built(self, res: object) -> None:
        self._finish_worker()
        self._set_busy(False)
        app = res.get("app") if isinstance(res, dict) else None
        if not isinstance(app, dict):
            return
        self._app_id = str(app.get("id") or self._app_id)
        self._connection = str(app.get("connection_name") or self._connection)
        self._title.setText(str(app.get("name") or _t("app.window_title")))
        self._render(str(app.get("html") or ""))

    def _on_failed(self, err: str) -> None:
        self._finish_worker()
        self._set_busy(False)
        dialog_warn(self, _t("app.window_title"), _t("app.compile_failed", error=str(err)[:200]))

    def _finish_worker(self) -> None:
        worker = self._worker
        if worker is not None:
            self._worker = None
            worker.wait()
            worker.deleteLater()

    # -- rendering ------------------------------------------------------------

    def _render(self, body_html: str) -> None:
        self._web.set_dashboard(body_html, self._run_fn())

    def _run_fn(self):
        from dbaide.charts.echarts import chart_spec_to_echarts_option
        from dbaide.desktop.components.chart_block import _theme_payload
        service = self._service

        def run(chart_id: str, params: dict[str, Any]) -> dict[str, Any]:
            res = service.dispatch("run_app_chart",
                                   {"app_id": self._app_id, "chart_id": chart_id, "params": params})
            spec = res.get("chart_spec") or {}
            return {"echarts_option": chart_spec_to_echarts_option(spec, theme=_theme_payload()),
                    "title": spec.get("title")}
        return run

    def _set_busy(self, busy: bool) -> None:
        self._send.setEnabled(not busy)
        self._refine.setEnabled(not busy)
        self._status.setText(_t("app.compiling") if busy else "")
