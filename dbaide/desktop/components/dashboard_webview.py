"""WebEngine host for an AI-authored interactive dashboard page.

Registers a QWebChannel bridge whose only entry point is ``query(chart_id,
params)`` — the page can invoke named recipes with parameter values, nothing
else. Data is produced by the injected ``run_fn`` (the deterministic runtime).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from dbaide.desktop.components.markdown_webview import try_create_webengine_view
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import echarts_script_src, webengine_html_base
from dbaide.rendering.dashboard_page import build_dashboard_page

# run_fn(chart_id, params) -> {"echarts_option": {...}, "title": str}  (may raise)
RunFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class _DashboardBridge(QObject):
    """The single, locked entry point the page can call. No raw SQL crosses it."""

    def __init__(self, run_fn: RunFn, parent=None) -> None:
        super().__init__(parent)
        self._run = run_fn

    @pyqtSlot(str, str, result=str)
    def query(self, chart_id: str, params_json: str) -> str:
        try:
            params = json.loads(params_json or "{}")
            if not isinstance(params, dict):
                params = {}
            return json.dumps(self._run(str(chart_id), params), ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — surface to the page, never crash the slot
            return json.dumps({"error": str(exc)[:200]}, ensure_ascii=False)


def _theme_payload() -> dict[str, str]:
    return {"text": Theme.TEXT, "muted": Theme.MUTED, "bg": Theme.BG, "panel": Theme.PANEL,
            "border": Theme.BORDER_SOFT, "accent": Theme.ACCENT}


class DashboardWebView(QWidget):
    """Renders an AI dashboard body and wires it to ``run_fn`` via the bridge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._view: Any = None
        self._channel: Any = None
        self._bridge: _DashboardBridge | None = None

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
        self._bridge = _DashboardBridge(run_fn, self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)
        page = build_dashboard_page(body_html, echarts_src=es, theme=_theme_payload())
        self._view.setHtml(page, webengine_html_base(es))
