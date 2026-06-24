"""AI dashboard studio: an interactive HTML dashboard you build and refine by chat.

The builder agent (seeded with the conversation's analysis) authors the page +
recipes; it renders in a WebChannel-wired WebEngine view. A chat box at the
bottom sends refinement instructions ("make the month a date range", "add a pie
chart by category") back to the agent, which rewrites the page.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.dashboard_webview import DashboardWebView
from dbaide.desktop.components.spinner import BusyAnimator, spinner_pixmap
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
        self._has_rendered = False
        self._busy = BusyAnimator(self._tick, parent=self)

        self.setStyleSheet(f"background:{Theme.BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # header: title + subtitle, with a small "updating" chip on the right ------
        head = QHBoxLayout()
        titlecol = QVBoxLayout()
        titlecol.setSpacing(2)
        self._title = QLabel(_t("app.window_title"))
        self._title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        self._subtitle = QLabel(_t("app.studio_subtitle"))
        self._subtitle.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent;")
        titlecol.addWidget(self._title)
        titlecol.addWidget(self._subtitle)
        head.addLayout(titlecol, 1)
        self._model = QComboBox()
        self._model.setMinimumWidth(150)
        self._model.setStyleSheet(
            f"QComboBox {{ background:{Theme.PANEL_2}; color:{Theme.TEXT}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:6px; padding:5px 9px; font-size:12px; }}")
        self._populate_models()
        head.addWidget(self._model)
        self._chip = QFrame()
        self._chip.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT}; border-radius:12px; }}")
        chip_l = QHBoxLayout(self._chip)
        chip_l.setContentsMargins(10, 4, 12, 4)
        chip_l.setSpacing(6)
        self._chip_spin = QLabel()
        chip_l.addWidget(self._chip_spin)
        chip_lbl = QLabel(_t("app.updating"))
        chip_lbl.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:11px; background:transparent; border:none;")
        chip_l.addWidget(chip_lbl)
        self._chip.setVisible(False)
        head.addWidget(self._chip)
        root.addLayout(head)

        # content: loading placeholder ↔ rendered dashboard -----------------------
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_loading())   # 0
        self._web = DashboardWebView()
        self._stack.addWidget(self._web)                # 1
        self._stack.addWidget(self._build_error())      # 2
        root.addWidget(self._stack, 1)

        # refine composer ---------------------------------------------------------
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:10px; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 4, 6, 4)
        bl.setSpacing(8)
        self._refine = QLineEdit()
        self._refine.setPlaceholderText(_t("app.refine_ph"))
        self._refine.setFrame(False)
        self._refine.setMinimumHeight(34)
        self._refine.setStyleSheet(
            f"QLineEdit {{ background:transparent; border:none; color:{Theme.TEXT}; font-size:13px; }}")
        self._refine.returnPressed.connect(self._on_refine)
        bl.addWidget(self._refine, 1)
        self._send = compact_button(_t("app.send"), primary=True, width=84)
        self._send.clicked.connect(self._on_refine)
        bl.addWidget(self._send)
        root.addWidget(bar)

    def _build_loading(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)
        self._loading_spin = QLabel()
        self._loading_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._loading_spin, alignment=Qt.AlignmentFlag.AlignCenter)
        msg = QLabel(_t("app.building_title"))
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color:{Theme.TEXT}; font-size:14px; font-weight:600; background:transparent;")
        lay.addWidget(msg)
        hint = QLabel(_t("app.building_hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setMaximumWidth(420)
        hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        lay.addWidget(hint, alignment=Qt.AlignmentFlag.AlignCenter)
        return w

    def _build_error(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        self._error_msg = QLabel("")
        self._error_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_msg.setWordWrap(True)
        self._error_msg.setMaximumWidth(460)
        self._error_msg.setStyleSheet(f"color:{Theme.RED}; font-size:13px; background:transparent;")
        lay.addWidget(self._error_msg)
        hint = QLabel(_t("app.error_hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        lay.addWidget(hint)
        return w

    def _tick(self) -> None:
        big = spinner_pixmap(self._busy.angle, size=30, color=Theme.ACCENT)
        self._loading_spin.setPixmap(big)
        self._chip_spin.setPixmap(spinner_pixmap(self._busy.angle, size=13, color=Theme.ACCENT))

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
        self._busy.stop()
        if self._worker is not None:
            self._worker.wait()
            self._worker.deleteLater()
            self._worker = None

    def closeEvent(self, e) -> None:  # noqa: N802
        self.shutdown()   # don't let a build QThread be destroyed mid-run on close
        super().closeEvent(e)

    # -- build / refine -------------------------------------------------------

    def _populate_models(self) -> None:
        try:
            boot = self._service.dispatch("bootstrap", {})
        except Exception:  # noqa: BLE001
            boot = {}
        models = boot.get("models") or []
        default = str(boot.get("default_model") or "")
        for m in models:
            nm = str(m.get("name") or "")
            if nm:
                self._model.addItem(nm, nm)
        if default:
            i = self._model.findData(default)
            if i >= 0:
                self._model.setCurrentIndex(i)

    def _current_model(self) -> str:
        return str(self._model.currentData() or "")

    def _build(self, payload: dict) -> None:
        if self._worker is not None:
            return
        payload.setdefault("model", self._current_model())   # generate with the chosen model
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
        if self._has_rendered:
            dialog_warn(self, _t("app.window_title"), _t("app.compile_failed", error=str(err)[:200]))
        else:
            # first build failed — show an error state, not a frozen loading spinner
            self._error_msg.setText(_t("app.compile_failed", error=str(err)[:200]))
            self._stack.setCurrentIndex(2)

    def _finish_worker(self) -> None:
        worker = self._worker
        if worker is not None:
            self._worker = None
            worker.wait()
            worker.deleteLater()

    # -- rendering ------------------------------------------------------------

    def _render(self, body_html: str) -> None:
        self._web.set_dashboard(body_html, self._run_fn())
        self._has_rendered = True
        self._stack.setCurrentWidget(self._web)

    def _run_fn(self):
        from dbaide.charts.echarts import chart_spec_to_echarts_option
        from dbaide.desktop.components.chart_block import _theme_payload
        service = self._service

        def run(chart_id: str, params: dict[str, Any]) -> dict[str, Any]:
            res = service.dispatch("run_app_chart",
                                   {"app_id": self._app_id, "chart_id": chart_id, "params": params})
            spec = res.get("chart_spec")
            option = chart_spec_to_echarts_option(spec, theme=_theme_payload()) if spec else None
            # return data too: kpi/table tiles render from rows; chart tiles use the option
            return {"echarts_option": option, "title": spec.get("title") if spec else None,
                    "columns": res.get("columns") or [], "rows": res.get("rows") or []}
        return run

    def _set_busy(self, busy: bool) -> None:
        self._send.setEnabled(not busy)
        self._refine.setEnabled(not busy)
        if busy:
            self._busy.start()
            if self._has_rendered:
                self._chip.setVisible(True)          # refine: keep the board, show a chip
            else:
                self._stack.setCurrentIndex(0)        # first build: loading state
        else:
            self._busy.stop()
            self._chip.setVisible(False)
