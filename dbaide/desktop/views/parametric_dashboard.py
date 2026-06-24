"""Interactive parameterized dashboard view.

A shared control bar drives several charts: changing a filter and pressing Apply
re-runs every chart through the deterministic runtime (on a worker thread, no
model call) and updates the charts in place.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.chart_block import build_chart_widget
from dbaide.desktop.components.param_controls import ParamControls
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t

_COLS = 2


class _AppRunWorker(QThread):
    chart_done = pyqtSignal(str, object)
    chart_failed = pyqtSignal(str, str)
    finished_all = pyqtSignal()

    def __init__(self, service, app_id: str, chart_ids: list[str], params: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._app_id = app_id
        self._chart_ids = list(chart_ids)
        self._params = dict(params)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for cid in self._chart_ids:
            if self._cancelled:
                break
            try:
                res = self._service.dispatch("run_app_chart",
                                             {"app_id": self._app_id, "chart_id": cid, "params": self._params})
                if not self._cancelled:
                    self.chart_done.emit(cid, res)
            except BaseException as exc:  # noqa: BLE001
                if not self._cancelled:
                    self.chart_failed.emit(cid, str(exc))
        self.finished_all.emit()


class _ChartCard(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL}; border:1px solid {Theme.BORDER_SOFT}; border-radius:8px; }}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 8, 12, 12)
        self._lay.setSpacing(6)
        head = QLabel(title)
        head.setFont(QFont("Inter", 12, QFont.Weight.DemiBold))
        head.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        head.setWordWrap(True)
        self._lay.addWidget(head)
        self._body: QWidget | None = None
        self.set_loading()

    def _swap(self, widget: QWidget) -> None:
        if self._body is not None:
            self._lay.removeWidget(self._body)
            self._body.deleteLater()
        self._body = widget
        self._lay.addWidget(widget, 1)

    def set_loading(self) -> None:
        lbl = QLabel(_t("app.running"))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setMinimumHeight(200)
        lbl.setStyleSheet(f"color:{Theme.MUTED}; background:transparent; padding:24px;")
        self._swap(lbl)

    def set_spec(self, spec: dict[str, Any]) -> None:
        try:
            widget = build_chart_widget(spec) if isinstance(spec, dict) else None
        except Exception:
            widget = None
        if widget is None:
            widget = QLabel(_t("conversation.chart_no_data"))
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setMinimumHeight(200)
            widget.setStyleSheet(f"color:{Theme.MUTED}; background:transparent; padding:24px;")
        self._swap(widget)

    def set_error(self, message: str) -> None:
        lbl = QLabel(_t("board.tile_error", error=str(message)[:140]))
        lbl.setWordWrap(True)
        lbl.setMinimumHeight(120)
        lbl.setStyleSheet(f"color:{Theme.RED}; background:transparent; padding:24px;")
        self._swap(lbl)


class ParametricDashboardView(QWidget):
    def __init__(self, service, app: dict[str, Any], controls: list[dict], defaults: dict, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._app_id = str(app.get("id") or "")
        self._charts = app.get("charts") or []
        self._worker: _AppRunWorker | None = None
        self._first_shown = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        title = QLabel(str(app.get("name") or _t("mode.dashboards")))
        title.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        root.addWidget(title)

        self._controls = ParamControls(controls, defaults)
        self._controls.applied.connect(self._run_all)
        root.addWidget(self._controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._cards: dict[str, _ChartCard] = {}
        for i, ch in enumerate(self._charts):
            cid = str(ch.get("chart_id") or "")
            card = _ChartCard(str(ch.get("title") or cid))
            self._cards[cid] = card
            grid.addWidget(card, i // _COLS, i % _COLS)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._first_shown:
            self._first_shown = True
            self._run_all(self._controls.values())   # initial render with defaults

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait()
            self._worker.deleteLater()
            self._worker = None

    def _run_all(self, params: dict[str, Any]) -> None:
        if self._worker is not None:
            return
        for card in self._cards.values():
            card.set_loading()
        worker = _AppRunWorker(self._service, self._app_id, list(self._cards.keys()), params or {}, self)
        worker.chart_done.connect(self._on_done)
        worker.chart_failed.connect(self._on_failed)
        worker.finished_all.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_done(self, cid: str, result: object) -> None:
        card = self._cards.get(cid)
        if card is not None and isinstance(result, dict):
            card.set_spec(result.get("chart_spec"))

    def _on_failed(self, cid: str, error: str) -> None:
        card = self._cards.get(cid)
        if card is not None:
            card.set_error(error)

    def _on_finished(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker.wait()
        worker.deleteLater()
