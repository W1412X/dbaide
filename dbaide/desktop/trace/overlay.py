"""Window-level trace overlay controller — single owner of drawer lifecycle."""

from __future__ import annotations

import weakref
from typing import Any, Callable

from PyQt6.QtCore import QTimer, QEvent, QObject
from PyQt6.QtWidgets import QWidget

from dbaide.desktop.trace.helpers import alive_widget, is_descendant
from dbaide.desktop.trace.session import TraceViewState


class TraceOverlayController(QObject):
    """One controller per top-level window; owns session state and the drawer widget."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._session = TraceViewState()
        self._drawer = None
        self._owner_ref: weakref.ReferenceType[QWidget] | None = None
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(48)
        self._relayout_timer.timeout.connect(self._debounced_relayout)
        window.installEventFilter(self)

    @classmethod
    def for_host(cls, host: QWidget) -> TraceOverlayController:
        window = host.window()
        ctrl = getattr(window, "_trace_overlay_ctrl", None)
        if ctrl is None or not isinstance(ctrl, TraceOverlayController):
            ctrl = TraceOverlayController(window)
            window._trace_overlay_ctrl = ctrl
        return ctrl

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._window and self._drawer is not None and not self._drawer.isHidden():
            if event.type() == QEvent.Type.Resize:
                self._relayout_timer.start()
            elif event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
                self.close()
        return False

    @property
    def session(self) -> TraceViewState:
        return self._session

    def drawer(self):
        return self._drawer

    def _ensure_drawer(self):
        if self._drawer is None:
            from dbaide.desktop.components.trace import TraceDrawerPanel

            self._drawer = TraceDrawerPanel(self._window, controller=self)
            self._window._trace_drawer_panel = self._drawer
        return self._drawer

    def toggle(
        self,
        host: QWidget,
        *,
        owner_widget: QWidget,
        owner_id: str,
        events: list[dict[str, Any]],
        live: bool,
        ok: bool,
        on_close: Callable[[], None] | None,
    ) -> bool:
        drawer = self._ensure_drawer()
        if not drawer.isHidden() and self._session.owner_id == str(owner_id or ""):
            self.close()
            return False
        self._bind_owner(owner_widget)
        self._session.load(
            owner_id=owner_id,
            events=events,
            live=live,
            ok=ok,
            on_close=on_close,
        )
        drawer.open_from_session(animate=True)
        return True

    def update(
        self,
        *,
        owner_id: str,
        events: list[dict[str, Any]],
        live: bool,
        ok: bool,
    ) -> None:
        if self._drawer is None or self._drawer.isHidden():
            return
        if self._session.owner_id != str(owner_id or ""):
            return
        self._session.apply_events(events, live=live, ok=ok)
        self._drawer.sync_from_session()

    def show_step_detail(self, data: dict[str, Any]) -> None:
        drawer = self._drawer
        if drawer is None or drawer.isHidden():
            return
        self._session.pin_detail(data)
        drawer.show_step_detail(data)

    def close(self) -> None:
        if self._drawer is not None:
            self._drawer.close_panel()
        self._session.notify_closed()
        self._unbind_owner()
        self._session.clear()

    def close_if_owner_in(self, host: QWidget) -> None:
        owner = self._owner_widget()
        if owner is not None and is_descendant(owner, host):
            self.close()

    def depends_on(self, host: QWidget) -> bool:
        return is_descendant(self._owner_widget(), host)

    def _debounced_relayout(self) -> None:
        if self._drawer is not None and not self._drawer.isHidden():
            self._drawer.relayout(raise_panel=False)

    def _owner_widget(self) -> QWidget | None:
        return alive_widget(self._owner_ref() if self._owner_ref is not None else None)

    def _bind_owner(self, owner_widget: QWidget) -> None:
        self._unbind_owner()
        owner = alive_widget(owner_widget)
        if owner is None:
            return
        self._owner_ref = weakref.ref(owner)
        panel_ref = weakref.ref(self)

        def _on_destroyed(*_args) -> None:
            ctrl = panel_ref()
            if ctrl is not None:
                ctrl.close()

        owner.destroyed.connect(_on_destroyed)

    def _unbind_owner(self) -> None:
        self._owner_ref = None


def close_trace_overlays(host: QWidget) -> None:
    TraceOverlayController.for_host(host).close()


def close_trace_overlays_for(host: QWidget) -> None:
    TraceOverlayController.for_host(host).close_if_owner_in(host)


def toggle_trace_drawer(
    host: QWidget,
    *,
    owner_widget: QWidget,
    owner_id: str,
    events: list[dict[str, Any]],
    live: bool,
    ok: bool,
    on_close,
) -> bool:
    return TraceOverlayController.for_host(host).toggle(
        host,
        owner_widget=owner_widget,
        owner_id=owner_id,
        events=events,
        live=live,
        ok=ok,
        on_close=on_close,
    )


def update_trace_drawer(
    host: QWidget,
    *,
    owner_id: str,
    events: list[dict[str, Any]],
    live: bool,
    ok: bool,
) -> None:
    TraceOverlayController.for_host(host).update(
        owner_id=owner_id,
        events=events,
        live=live,
        ok=ok,
    )


def show_trace_detail(host: QWidget, data: dict) -> None:
    TraceOverlayController.for_host(host).show_step_detail(data)
