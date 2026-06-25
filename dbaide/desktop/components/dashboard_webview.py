"""WebEngine host for an AI-authored interactive dashboard page.

Registers a QWebChannel bridge whose only entry point is ``query(chart_id,
params)`` — the page can invoke named recipes with parameter values, nothing
else. Data is produced by the injected ``run_fn`` (the deterministic runtime).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from dbaide.desktop.components.markdown_webview import try_create_webengine_view
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import echarts_script_src, webengine_html_base
from dbaide.rendering.dashboard_page import build_dashboard_page

# run_fn(chart_id, params) -> {"echarts_option": {...}, "title": str}  (may raise)
RunFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class _QueryRunnable(QRunnable):
    """Runs one recipe off the GUI thread, then delivers the JSON back to the page."""

    def __init__(self, bridge: "_DashboardBridge", token: str, chart_id: str, params: dict) -> None:
        super().__init__()
        self._bridge = bridge
        self._token = token
        self._chart_id = chart_id
        self._params = params

    def run(self) -> None:  # worker thread
        try:
            payload = json.dumps(self._bridge._run(self._chart_id, self._params),
                                 ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — surface to the page, never crash the worker
            payload = json.dumps({"error": str(exc)[:200]}, ensure_ascii=False)
        # marshal back to the main thread (queued) → resultReady → QWebChannel → JS
        self._bridge._deliver.emit(self._token, payload)


class _DashboardBridge(QObject):
    """The single, locked entry point the page can call. No raw SQL crosses it.

    Async: the page calls ``request(token, chart_id, params)`` (a void slot), the SQL
    runs on a worker thread, and the result is delivered via the ``resultReady`` signal
    — so a dashboard with many tiles never freezes the GUI while it loads.
    """

    resultReady = pyqtSignal(str, str)   # (token, payload_json) — consumed by the page
    _deliver = pyqtSignal(str, str)      # internal: worker thread → main thread re-emit

    def __init__(self, run_fn: RunFn, pool: QThreadPool, parent=None) -> None:
        super().__init__(parent)
        self._run = run_fn
        self._pool = pool   # shared, owned by the view (not per-bridge → no use-after-free on re-render)
        self._deliver.connect(self.resultReady)   # queued onto the main thread

    @pyqtSlot(str, str, str)
    def request(self, token: str, chart_id: str, params_json: str) -> None:
        try:
            params = json.loads(params_json or "{}")
            if not isinstance(params, dict):
                params = {}
        except Exception:  # noqa: BLE001
            params = {}
        self._pool.start(_QueryRunnable(self, str(token), str(chart_id), params))


def _theme_payload() -> dict[str, str]:
    # the live app palette, injected into the page as CSS variables so an AI-authored
    # dashboard matches the current theme (and re-injects if the theme changes)
    return {"text": Theme.TEXT, "text2": Theme.TEXT_2, "muted": Theme.MUTED,
            "bg": Theme.BG, "panel": Theme.PANEL, "panel2": Theme.PANEL_2,
            "border": Theme.BORDER_SOFT, "accent": Theme.ACCENT, "accent_text": Theme.ACCENT_TEXT}


class DashboardWebView(QWidget):
    """Renders an AI dashboard body and wires it to ``run_fn`` via the bridge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._view: Any = None
        self._channel: Any = None
        self._bridge: _DashboardBridge | None = None
        self._pool = QThreadPool(self)   # one pool for the view's lifetime
        self._pool.setMaxThreadCount(4)

    def set_dashboard(self, body_html: str, run_fn: RunFn) -> None:
        view_cls = try_create_webengine_view()
        if view_cls is None:
            self._lay.addWidget(QLabel("WebEngine unavailable — cannot render dashboard."))
            return
        if self._view is None:
            self._view = view_cls(self)
            self._lay.addWidget(self._view)
        from PyQt6.QtWebChannel import QWebChannel

        es = echarts_script_src()
        # A new bridge per render; the OLD bridge stays parented (NOT deleteLater'd) so an
        # in-flight worker can't emit on a freed object. Its stale results have no live page
        # listener after setHtml, so they're harmless; the shared pool is reused.
        self._bridge = _DashboardBridge(run_fn, self._pool, self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)
        page = build_dashboard_page(body_html, echarts_src=es, theme=_theme_payload())
        self._view.setHtml(page, webengine_html_base(es))
