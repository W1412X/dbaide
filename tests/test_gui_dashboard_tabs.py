"""Dashboards UX: opening a saved board is view-only (no model picker / refine box),
generating is edit mode, and opened boards are shown as tabs."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubSvc:
    def dispatch(self, action, payload=None):
        payload = payload or {}
        if action == "bootstrap":
            return {"models": [{"name": "gpt"}], "default_model": "gpt"}
        if action == "get_dashboard_app":
            return {"app": {"id": payload.get("id"), "name": f"Board {payload.get('id')}",
                            "connection_name": "shop", "html": "<div>b</div>"}}
        if action == "list_dashboard_apps":
            return {"apps": [{"id": "a1", "name": "Sales", "charts": 3},
                             {"id": "a2", "name": "Refunds", "charts": 2}]}
        if action == "build_dashboard_app":
            return {"app": {"id": "gen1", "name": "New", "connection_name": "shop", "html": "<div></div>"}}
        return {}


def test_studio_view_vs_edit_mode(qapp):
    from dbaide.desktop.views.parametric_dashboard import ParametricDashboardStudio
    s = ParametricDashboardStudio(_StubSvc())
    # a fresh studio is ready to generate → edit controls visible, no "Edit" button
    assert s.is_editing() is True
    assert not s._model.isHidden() and not s._composer.isHidden() and s._edit_btn.isHidden()
    # opening a SAVED dashboard is view-only: model picker + refine box hidden, "Edit" shown
    s.open_existing("a1")
    assert s.is_editing() is False
    assert s._model.isHidden() and s._composer.isHidden() and not s._edit_btn.isHidden()
    # clicking "Edit" reveals the generate/refine controls again
    s.set_edit_mode(True)
    assert not s._model.isHidden() and not s._composer.isHidden() and s._edit_btn.isHidden()
    s.shutdown()
    qapp.processEvents()


def test_view_only_tab_skips_bootstrap_and_post_shutdown_is_safe(qapp):
    from dbaide.desktop.views.parametric_dashboard import ParametricDashboardStudio

    class _CountingSvc(_StubSvc):
        def __init__(self):
            self.boot = 0

        def dispatch(self, action, payload=None):
            if action == "bootstrap":
                self.boot += 1
            return super().dispatch(action, payload)

    svc = _CountingSvc()
    s = ParametricDashboardStudio(svc)
    assert svc.boot == 0                       # models populated lazily (bootstrap reads disk)
    s.open_existing("a1")
    assert svc.boot == 0 and s._model.count() == 0   # view-only never pays bootstrap
    s._enter_edit()
    assert svc.boot == 1 and s._model.count() == 1   # populated once on first edit
    s._enter_edit()
    assert svc.boot == 1                       # cached
    # a build that finishes after shutdown must not render into the torn-down view
    s.shutdown()
    s._render("<div>x</div>")
    s._on_built({"app": {"id": "z", "name": "Z", "html": "<d></d>"}})
    assert s.app_id() == "a1"                   # unchanged, no crash
    qapp.processEvents()


def test_dashboards_view_opens_boards_as_tabs(qapp):
    from dbaide.desktop.views.dashboards_view import DashboardsView
    v = DashboardsView(_StubSvc())
    assert v._stack.currentIndex() == 0  # starts on the gallery
    v._open("a1")
    assert v._stack.currentIndex() == 1 and v._tabs.count() == 1
    assert v._tabs.widget(0).is_editing() is False  # opened board = view-only tab
    v._open("a2")
    assert v._tabs.count() == 2
    v._open("a1")  # already open → focus its tab, don't duplicate
    assert v._tabs.count() == 2 and v._tabs.currentIndex() == 0
    # generating opens a new tab in edit mode
    g = v.open_generate(name="New", connection_name="shop", context=[{"sql": "SELECT 1"}], instruction="b")
    assert v._tabs.count() == 3 and g.is_editing() is True
    # closing a tab cleans it up; closing the last returns to the gallery
    v._close_tab(0)
    qapp.processEvents()
    assert v._tabs.count() == 2
    while v._tabs.count():
        v._close_tab(0)
        qapp.processEvents()
    assert v._stack.currentIndex() == 0
    v.shutdown()
    qapp.processEvents()
