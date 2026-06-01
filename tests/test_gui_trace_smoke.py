"""Offscreen smoke tests for the rewritten trace UI, dialogs, and bus wiring.

These instantiate real Qt widgets (offscreen) and drive them with events to catch
runtime Qt errors that py_compile cannot. Skipped automatically when PyQt6 is absent.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dbaide.agent.progress_events import progress_event, subagent_event  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_trace_panel_live_then_finalize(qapp):
    from dbaide.desktop.components.trace import TracePanel

    panel = TracePanel()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="loop", title="started", status="running", kind="agent"))
    panel.append_live_event(progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1))
    panel.append_live_event(subagent_event(agent="schema_link", title="scanning", parent="discover_schema"))
    panel.append_live_event(progress_event(stage="discover_schema", title="done", status="completed", kind="tool", step=1, duration_ms=12))
    panel.append_live_event(progress_event(stage="execute_sql", title="Calling", status="running", kind="tool", step=2))
    panel.append_live_event(subagent_event(agent="risk", title="auto_execute", parent="execute_sql", status="completed"))
    panel.append_live_event(progress_event(stage="execute_sql", title="done", status="completed", kind="tool", step=2, duration_ms=40))
    panel.end_live()
    # Summary row + 2 step rows.
    assert panel.topLevelItemCount() == 3
    assert not panel.is_empty()
    # Summary row shows a coherent line.
    assert "step" in panel.topLevelItem(0).text(1).lower() or "done" in panel.topLevelItem(0).text(1).lower()


def test_trace_panel_load_persisted_events(qapp):
    from dbaide.desktop.components.trace import TracePanel

    panel = TracePanel()
    panel.load_events([
        {"stage": "workflow_started", "title": "s", "status": "completed", "kind": "agent", "timestamp": 1.0},
        {"stage": "execute_sql", "title": "ran", "status": "completed", "kind": "tool", "timestamp": 2.0, "duration_ms": 5},
        {"stage": "workflow_completed", "title": "done", "status": "completed", "kind": "agent", "timestamp": 3.0},
    ])
    # Framing events filtered; one real step + summary.
    assert panel.topLevelItemCount() == 2


def test_build_dialog_options(qapp):
    from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog

    dlg = BuildAssetsDialog(
        connection_name="prod",
        databases=[{"name": "main", "has_assets": False}, {"name": "shop", "has_assets": True}],
        load_profile="production",
        default_profile_mode="light",
        default_max_workers=1,
    )
    assert set(dlg.selected_databases()) == {"main", "shop"}
    opts = dlg.build_options()
    assert opts["profile_mode"] == "light"
    assert opts["max_workers"] == 1
    assert "timeout" in opts


def test_settings_resources_page_roundtrip(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog

    captured = {}
    dlg = SettingsDialog(
        connections=[],
        models=[],
        resource_defaults={"values": {"max_inflight_queries": 5}, "presets": {"production": {"max_inflight_queries": 2}}},
        initial_page="resources",
    )
    dlg.resource_saved.connect(lambda payload: captured.update(payload))
    # Prefilled value shows.
    assert dlg._resource_spins["max_inflight_queries"].value() == 5
    dlg._resource_spins["max_row_limit"].setValue(321)
    dlg._save_resources()
    assert captured["values"]["max_inflight_queries"] == 5
    assert captured["values"]["max_row_limit"] == 321


def test_connection_form_includes_load_profile(qapp):
    from dbaide.desktop.dialogs.connection import ConnectionForm

    form = ConnectionForm(conn_type="sqlite")
    form.load_profile.setCurrentText("dev")
    assert form.payload()["load_profile"] == "dev"


def test_main_window_constructs_and_bus_wired(qapp, tmp_path):
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.event_bus import ASSETS_CHANGED, JOINS_CHANGED, MODELS_CHANGED
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    conn.commit()
    conn.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))

    win = MainWindow(service)
    # Bus is wired with the expected subscriptions.
    assert win.bus.subscriber_count(ASSETS_CHANGED) == 1
    assert win.bus.subscriber_count(MODELS_CHANGED) == 1
    assert win.bus.subscriber_count(JOINS_CHANGED) == 1
    # Emitting must not raise (handlers spawn background work / refresh).
    win.bus.emit(JOINS_CHANGED, {"instance": "local"})
    win.deleteLater()
