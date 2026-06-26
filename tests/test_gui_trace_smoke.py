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


def _first_opaque_hex(icon, size: int = 18) -> str:
    image = icon.pixmap(size, size).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 0:
                return color.name()
    return ""


def test_trace_panel_live_then_finalize(qapp):
    from dbaide.desktop.components.trace import InlineTrace

    panel = InlineTrace()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="loop", title="started", status="running", kind="agent"))
    panel.append_live_event(progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1))
    panel.append_live_event(subagent_event(agent="schema_link", title="db1 kept 3", parent="discover_schema", node_id="schema:db1", status="completed"))
    panel.append_live_event(subagent_event(agent="schema_link", title="db2 kept 1", parent="discover_schema", node_id="schema:db2", status="completed"))
    panel.append_live_event(progress_event(stage="discover_schema", title="done", status="completed", kind="tool", step=1, duration_ms=12))
    panel.append_live_event(progress_event(stage="execute_sql", title="Calling", status="running", kind="tool", step=2))
    panel.append_live_event(subagent_event(agent="risk", title="auto_execute", parent="execute_sql", node_id="risk:1", status="completed"))
    panel.append_live_event(progress_event(stage="execute_sql", title="done", status="completed", kind="tool", step=2, duration_ms=40))
    panel.end_live()
    tree = panel._tree
    # Summary row + flat chronological cards (tools + parallel substeps).
    assert tree.topLevelItemCount() == 6
    assert not panel.is_empty()
    # Substeps are sibling timeline rows, not nested under the tool card.
    assert tree.topLevelItem(1).childCount() == 0
    assert tree.topLevelItem(2).childCount() == 0
    assert tree.topLevelItem(3).childCount() == 0


def test_live_trace_rebuild_does_not_orphan_cards_as_windows(qapp):
    """Timeline rebuilds must not call setParent(None) — that spawns stray macOS windows."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from dbaide.desktop.components.trace import InlineTrace, _TraceStepCard

    host = QWidget()
    host.setObjectName("traceRebuildHost")
    host.resize(520, 640)
    host.show()
    qapp.processEvents()

    trace = InlineTrace(host, show_header=False)
    lay = QVBoxLayout(host)
    lay.addWidget(trace)
    trace.show()
    qapp.processEvents()

    def _orphan_cards() -> list:
        return [
            w
            for w in QApplication.topLevelWidgets()
            if isinstance(w, _TraceStepCard) or (
                w.parent() is None and w is not host and w.objectName() == "traceTimelineCard"
            )
        ]

    def _flush_trace(trace) -> None:
        trace._render_timer.stop()
        trace._render()

    events = [
        progress_event(stage="loop", title="started", status="running", kind="agent"),
        progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1),
    ]
    trace.set_events(events, live=True)
    _flush_trace(trace)
    qapp.processEvents()
    assert trace._cards, "expected timeline cards after set_events"

    for i in range(8):
        events.append(
            progress_event(
                stage="execute_sql",
                title=f"Calling {i}",
                status="running" if i % 2 == 0 else "completed",
                kind="tool",
                step=2,
                duration_ms=4 if i % 2 else 0,
            )
        )
        trace.set_events(events, live=True)
        _flush_trace(trace)
        qapp.processEvents()
        assert not _orphan_cards(), "timeline rebuild leaked top-level card windows"

    trace.set_events(events, live=False)
    _flush_trace(trace)
    assert not _orphan_cards()
    host.deleteLater()
    qapp.processEvents()


def test_trace_detail_dialog_shows_step(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    from dbaide.desktop.components.trace import InlineTrace, TraceDetailPanel, trace_step_raw_export

    host = QWidget()
    host.resize(900, 640)
    panel = InlineTrace()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="execute_sql", title="ran query", detail="SELECT 1",
                                            status="completed", kind="tool", step=1, duration_ms=7))
    panel.end_live()  # flush the coalesced render
    step = panel._tree.topLevelItem(1)
    data = step.data(0, Qt.ItemDataRole.UserRole)
    dlg = TraceDetailPanel(host)
    dlg.show_detail(data)
    text = dlg._body.toPlainText()
    assert "execute_sql" in text
    assert "SELECT 1" in text
    assert "7 ms" in text
    exported = trace_step_raw_export(data)
    assert "SELECT 1" in exported
    assert "event" in exported
    assert dlg._raw_text.strip()
    dlg._do_copy_raw()
    from PyQt6.QtWidgets import QApplication
    assert "SELECT 1" in QApplication.clipboard().text()


def test_trace_step_raw_export_includes_thought(qapp):
    from dbaide.desktop.components.trace import trace_step_raw_export

    data = {
        "node_id": "step:1",
        "stage": "execute_sql",
        "thought": "count orders",
        "raw": {"sql": "SELECT 1", "row_count": 1},
    }
    exported = trace_step_raw_export(data)
    assert "count orders" in exported
    assert "SELECT 1" in exported


def test_detail_json_is_bounded_but_copy_raw_is_full(qapp):
    """A step with a huge output payload must not render unbounded into the detail
    panel (would freeze QTextBrowser), but Copy raw still exports the full event."""
    from dbaide.desktop.components.trace import _json_text, _detail_html, trace_step_raw_export

    rendered = _json_text("x" * 50_000)
    assert len(rendered) < 25_000 and "truncated for display" in rendered

    big = {"raw": {"output": "y" * 50_000}, "title": "big step", "node_type": "info"}
    html = _detail_html(big)
    assert "truncated for display" in html
    # Copy raw keeps the full payload (not truncated to the display cap).
    assert trace_step_raw_export(big).count("y") >= 50_000


def test_trace_panel_load_persisted_events(qapp):
    from dbaide.desktop.components.trace import InlineTrace

    panel = InlineTrace()
    panel.set_events([
        {"stage": "workflow_started", "title": "s", "status": "completed", "kind": "agent", "timestamp": 1.0},
        {"stage": "execute_sql", "title": "ran", "status": "completed", "kind": "tool", "timestamp": 2.0, "duration_ms": 5},
        {"stage": "workflow_completed", "title": "done", "status": "completed", "kind": "agent", "timestamp": 3.0},
    ], live=False)
    # Framing events filtered; one real step + summary.
    assert panel._tree.topLevelItemCount() == 2
    assert panel.copy_text()  # copy works on the new widget


def test_conversation_turn_shows_agenda_from_trace(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    view = ConversationView()
    view.begin_turn("check orders")
    view.append_trace_event({
        "stage": "update_agenda",
        "title": "update_agenda done",
        "status": "completed",
        "kind": "tool",
        "step": 1,
        "result_data": {
            "summary": "1/2 done · 1 in progress",
            "agenda": {
                "items": [
                    {"id": "task:1", "title": "Inspect schema", "status": "done", "kind": "schema"},
                    {"id": "task:2", "title": "Write SQL", "status": "in_progress", "kind": "sql"},
                ]
            },
        },
    })
    assert view._current_turn is not None
    assert view._current_turn._agenda_box.isHidden() is False
    view.complete_turn(answer="done", trace_events=view._current_record["events"])
    block = view._turns[-1]
    assert any(event.get("stage") == "update_agenda" for event in block["events"])


def test_agenda_status_text_uses_i18n_keys(qapp):
    from dbaide.desktop.components.conversation import _agenda_status_text
    from dbaide.i18n import set_language

    set_language("zh")
    try:
        assert _agenda_status_text("pending") == "待办"
        assert _agenda_status_text("in_progress") == "进行中"
        assert _agenda_status_text("done") == "完成"
        assert _agenda_status_text("dropped") == "已放弃"
    finally:
        set_language("en")


def test_build_dialog_options(qapp):
    from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog

    dlg = BuildAssetsDialog(
        connection_name="prod",
        databases=[{"name": "main", "has_assets": False}, {"name": "shop", "has_assets": True}],
        default_max_workers=1,
    )
    assert set(dlg.selected_databases()) == {"main", "shop"}
    opts = dlg.build_options()
    assert "profile_mode" not in opts
    assert opts["max_workers"] == 1
    assert "timeout" in opts


def test_sidebar_build_progress_tracks_real_counts(qapp):
    from dbaide.desktop.views.sidebar import Sidebar

    sidebar = Sidebar()
    sidebar.start_build_progress("Building")
    sidebar.update_build_progress({
        "stage": "build_assets",
        "title": "main · 0/2 tables",
        "status": "running",
        "node_id": "build:db:main",
        "database": "main",
        "completed_tables": 0,
        "total_tables": 2,
    })
    sidebar._flush_build_progress()
    sidebar.update_build_progress({
        "stage": "build_assets",
        "title": "main · 1/2 tables · users",
        "status": "running",
        "node_id": "build:db:main",
        "database": "main",
        "completed_tables": 1,
        "total_tables": 2,
        "current_table": "users",
    })
    sidebar._flush_build_progress()
    qapp.processEvents()
    assert not sidebar._build_progress.isHidden()
    assert sidebar._build_progress_title.text() == "Building"
    assert "users" in sidebar._build_progress_detail.text()
    assert sidebar._build_progress_count.text() == "1/2"

    # node_id-only database key (regression: must not crash the flush timer)
    sidebar.update_build_progress({
        "stage": "build_assets",
        "title": "main · 2/2 tables · orders",
        "status": "running",
        "node_id": "build:db:main",
        "completed_tables": 2,
        "total_tables": 2,
    })
    sidebar._flush_build_progress()
    qapp.processEvents()
    assert sidebar._build_progress_count.text() == "2/2"

    sidebar.finish_build_progress("done")
    qapp.processEvents()
    assert sidebar._build_progress_count.text() == "2/2"


def test_sidebar_filter_updates_visibility_without_rebuild(qapp, monkeypatch):
    from dbaide.desktop.views.sidebar import Sidebar

    sidebar = Sidebar()
    rows = [{
        "kind": "database",
        "name": "shop",
        "path": "shop",
        "children": [{
            "kind": "table",
            "name": "orders",
            "path": "shop.orders",
            "column_count": 2,
            "children": [
                {"kind": "column", "name": "order_id", "path": "shop.orders.order_id", "data_type": "bigint"},
                {"kind": "column", "name": "buyer_name", "path": "shop.orders.buyer_name", "data_type": "text"},
            ],
        }],
    }]
    sidebar.load_schema(rows)
    root = sidebar.tree.topLevelItem(0)
    table = root.child(0) if root is not None else None
    assert root is not None and table is not None

    monkeypatch.setattr(
        sidebar,
        "_render",
        lambda _rows: (_ for _ in ()).throw(AssertionError("_render should not be called")),
    )

    sidebar._filter_tree("buyer")
    assert sidebar.tree.topLevelItem(0) is root
    assert not root.isHidden()
    assert not table.isHidden()
    assert table.child(0).isHidden()
    assert not table.child(1).isHidden()

    sidebar._filter_tree("")
    assert sidebar.tree.topLevelItem(0) is root
    assert not table.child(0).isHidden()


def test_sidebar_tree_uses_stable_nonanimated_rows(qapp):
    from dbaide.desktop.views.sidebar import Sidebar

    sidebar = Sidebar()
    assert sidebar.tree.isAnimated() is False
    assert sidebar.tree.uniformRowHeights() is True


def test_empty_schema_projection_uses_inline_progress(qapp):
    import dbaide.desktop.views.main_window as mw

    win = mw.MainWindow.__new__(mw.MainWindow)
    win._projected = set()
    win.schema_rows = []
    win.current_connection = lambda: "new_conn"  # type: ignore[method-assign]
    schema_messages: list[tuple[str, bool]] = []
    progress_starts: list[str] = []
    win._ensure_ui_state = lambda: type("Ui", (), {  # type: ignore[method-assign]
        "schema_loading": lambda _self, message, update=False: schema_messages.append((message, update)),
        "schema_build_progress_start": lambda _self, message: progress_starts.append(message),
    })()
    progress_calls: list[tuple[str, object]] = []
    fetched: list[str] = []
    failures: list[tuple[str, str]] = []
    finished: list[tuple[str, object]] = []
    win._fetch_schema_after_project = lambda name: fetched.append(name)  # type: ignore[method-assign]
    win._project_failed = lambda name, message: failures.append((name, message))  # type: ignore[method-assign]
    win._handle_asset_build_progress = lambda name, message: progress_calls.append((name, message))  # type: ignore[method-assign]
    win._finish_asset_build_progress = lambda name, result: finished.append((name, result))  # type: ignore[method-assign]
    win.toast = lambda message: None  # type: ignore[method-assign]
    background_calls: list[dict[str, object]] = []

    def run_background(action, payload, on_success, *, on_error=None, on_progress=None):
        background_calls.append({
            "action": action,
            "payload": payload,
            "on_success": on_success,
            "on_error": on_error,
            "on_progress": on_progress,
        })
        return "handle-1"

    win._run_background = run_background  # type: ignore[method-assign]

    win._on_schema_rows("new_conn", [])

    assert "new_conn" in win._projected
    assert background_calls[0]["action"] == "project_instance"
    assert background_calls[0]["payload"] == {"name": "new_conn"}
    assert progress_starts == [mw._i18n_t("schema.projecting")]

    progress = background_calls[0]["on_progress"]
    assert callable(progress)
    progress({"title": "main · 1/2 tables", "completed_tables": 1, "total_tables": 2})
    assert progress_calls[-1][0] == "new_conn"
    assert progress_calls[-1][1]["completed_tables"] == 1
    assert schema_messages == []

    done = background_calls[0]["on_success"]
    assert callable(done)
    done({"stats": {"tables": 2}})
    assert finished == [("new_conn", {"stats": {"tables": 2}})]
    assert fetched == ["new_conn"]
    assert failures == []


def test_settings_resources_page_roundtrip(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog

    captured = {}
    dlg = SettingsDialog(
        connections=[],
        models=[],
        resource_defaults={
            "values": {"max_inflight_queries": 5},
            "presets": {"production": {"max_inflight_queries": 2, "session_uncompressed_turns": 2}},
        },
        initial_page="resources",
    )
    dlg.resource_saved.connect(lambda payload: captured.update(payload))
    # Prefilled value shows.
    assert dlg._resource_spins["max_inflight_queries"].value() == 5
    assert "session_uncompressed_turns" in dlg._resource_spins
    dlg._resource_spins["max_row_limit"].setValue(321)
    dlg._resource_spins["session_uncompressed_turns"].setValue(4)
    dlg._save_resources()
    assert captured["values"]["max_inflight_queries"] == 5
    assert captured["values"]["max_row_limit"] == 321
    assert captured["values"]["session_uncompressed_turns"] == 4


def test_settings_new_connection_and_model_are_explicit_drafts(qapp):
    from dbaide.i18n import set_language
    from dbaide.desktop.dialogs.settings import SettingsDialog

    set_language("en")
    dlg = SettingsDialog(
        connections=[{"name": "local", "type": "sqlite", "path": "a.db"}],
        models=[{"name": "default", "provider": "none", "model": ""}],
    )

    dlg._add_connection()
    assert dlg.conn_list.currentItem().text() == "New connection"
    assert dlg.conn_form.payload()["name"] == ""
    assert dlg.save_conn_btn.text() == "Create"
    assert dlg.conn_more.isEnabled() is False

    dlg._add_model()
    assert dlg.model_list.currentItem().text() == "New model"
    assert dlg.model_form.payload()["name"] == ""
    assert dlg.save_model_btn.text() == "Create"
    assert dlg.model_more.isEnabled() is False


def test_settings_delete_and_default_wait_for_controller_success(qapp, monkeypatch):
    from dbaide.desktop.dialogs import message_dialog

    from dbaide.i18n import set_language
    from dbaide.desktop.dialogs.settings import SettingsDialog

    set_language("en")
    dlg = SettingsDialog(
        connections=[
            {"name": "local", "type": "sqlite", "path": "a.db"},
            {"name": "remote", "type": "sqlite", "path": "b.db"},
        ],
        models=[
            {"name": "default", "provider": "none", "model": ""},
            {"name": "alt", "provider": "none", "model": ""},
        ],
        default_connection="local",
        default_model="default",
    )
    # Patch the module-level name that settings.py imported, not just the
    # source module, so the already-resolved ``dialog_confirm`` alias is replaced.
    from dbaide.desktop.dialogs import settings as _settings_mod
    monkeypatch.setattr(_settings_mod, "dialog_confirm", lambda *a, **k: True)
    deleted_connections: list[str] = []
    deleted_models: list[str] = []
    saved_connections: list[dict] = []
    saved_models: list[dict] = []
    dlg.connection_deleted.connect(deleted_connections.append)
    dlg.model_deleted.connect(deleted_models.append)
    dlg.connection_saved.connect(saved_connections.append)
    dlg.model_saved.connect(saved_models.append)

    dlg.conn_form.load(dlg._connections["remote"])
    dlg._remove_connection()
    assert deleted_connections == ["remote"]
    assert "remote" in dlg._connections
    dlg.remove_connection_entry("remote")
    assert "remote" not in dlg._connections

    dlg.model_form.load(dlg._models["alt"])
    dlg._remove_model()
    assert deleted_models == ["alt"]
    assert "alt" in dlg._models
    dlg.remove_model_entry("alt")
    assert "alt" not in dlg._models

    dlg.conn_form.load(dlg._connections["local"])
    dlg._default_connection = "other"
    dlg._set_default_connection()
    assert saved_connections[-1]["make_default"] is True
    assert dlg._default_connection == "other"

    dlg.model_form.load(dlg._models["default"])
    dlg._default_model = "other"
    dlg._set_default_model()
    assert saved_models[-1]["make_default"] is True
    assert dlg._default_model == "other"


def test_message_dialog_sizes_wrapped_body_to_content(qapp):
    from dbaide.desktop.dialogs.message_dialog import MessageDialog

    message = "删除连接「" + ("production-readonly-" * 4) + "」？"
    dlg = MessageDialog(None, "设置", message, confirm=True)
    qapp.processEvents()

    doc_height = int(dlg._body.document().documentLayout().documentSize().height())
    assert dlg._body.height() >= doc_height
    assert dlg._body.toPlainText() == message


def test_message_dialog_caps_very_long_body_with_scroll(qapp):
    from dbaide.desktop.dialogs.message_dialog import MessageDialog, _MAX_BODY_HEIGHT

    message = "\n".join(f"warning {i}: SELECT * FROM orders WHERE created_at >= now()" for i in range(40))
    dlg = MessageDialog(None, "Risky SQL", message, confirm=True)
    qapp.processEvents()

    doc_height = int(dlg._body.document().documentLayout().documentSize().height())
    assert doc_height > _MAX_BODY_HEIGHT
    assert dlg._body.height() == _MAX_BODY_HEIGHT


def test_connection_form_includes_load_profile(qapp):
    from dbaide.desktop.dialogs.connection import ConnectionForm

    form = ConnectionForm(conn_type="sqlite")
    form.load_profile.setCurrentText("dev")
    assert form.payload()["load_profile"] == "dev"


def test_connection_form_includes_session_timezone(qapp):
    from dbaide.desktop.dialogs.connection import ConnectionForm

    form = ConnectionForm(conn_type="mysql")
    form.load({"type": "mysql", "session_timezone": "+08:00"})
    assert form.payload()["session_timezone"] == "+08:00"


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
    # Construction kicks off a background bootstrap worker; drain it and deliver its
    # queued signals to the live window before tearing down (else it fires at a
    # half-destroyed receiver and crashes Qt during interpreter shutdown).
    from PyQt6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(3000)
    qapp.processEvents()
    win.deleteLater()
    qapp.processEvents()




def test_copy_text_exports_structured_trace_with_sql(qapp):
    from dbaide.desktop.components.trace import InlineTrace
    panel = InlineTrace()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="decision", title="count paid", status="completed", kind="decision"))
    panel.append_live_event(progress_event(stage="retrieve_schema_context", title="retrieve_schema_context done", status="completed",
                                           kind="tool", step=1, detail="orders(id, amount)"))
    panel.append_live_event({"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                             "kind": "tool", "step": 2, "sql": "SELECT COUNT(*)\nFROM orders\nWHERE status='paid'",
                             "row_count": 3, "duration_ms": 7})
    panel.end_live()
    text = panel.copy_text()
    assert "✓" in text
    assert "retrieve_schema_context" in text or "Reading schema evidence" in text
    assert "orders(id, amount)" in text          # detail included
    assert "SELECT COUNT(*)" in text and "WHERE status='paid'" in text  # full SQL, multi-line
    # empty trace → empty export
    assert InlineTrace().copy_text() == ""


def test_turn_trace_chip_toggles_drawer(qapp):
    """Clicking a completed turn's chip opens the trace drawer; clicking again closes it."""
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("count paid orders")
    # stream a couple of live events into the open turn
    conv.append_trace_event({"stage": "execute_sql", "title": "Calling", "status": "running",
                             "kind": "tool", "step": 1})
    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                       "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM orders", "duration_ms": 4}],
        ok=True,
    )
    turn = conv._turns[-1]
    assert turn  # record exists
    # The most recently completed TurnBlock is reachable via the layout; grab it.
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(conv.window(), "_trace_drawer_panel", None)
    assert drawer is not None
    assert drawer.isHidden() is False
    assert drawer._timeline.is_empty() is False
    assert block.status._expanded is True
    block._toggle_trace()
    if drawer._anim is not None:
        drawer._anim.setCurrentTime(drawer._anim.duration())
        qapp.processEvents()
    assert drawer.isHidden() is True
    assert block.status._expanded is False
    conv.deleteLater()
    qapp.processEvents()


def test_trace_drawer_step_opens_bottom_detail(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("count paid orders")
    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[{
            "stage": "execute_sql",
            "title": "execute_sql done",
            "status": "completed",
            "kind": "tool",
            "step": 1,
            "sql": "SELECT COUNT(*) FROM orders",
            "duration_ms": 4,
        }],
        ok=True,
    )
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(conv.window(), "_trace_drawer_panel", None)
    assert drawer is not None
    card = drawer._timeline._cards[0]
    drawer.show_step_detail(card.trace_data())
    assert drawer._detail.isHidden() is False
    assert "SELECT COUNT(*) FROM orders" in drawer._detail._body.toPlainText()
    conv.deleteLater()
    qapp.processEvents()


def test_trace_drawer_does_not_auto_open_detail_on_live_updates(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("count paid orders")
    conv.append_trace_event({"stage": "execute_sql", "title": "Calling", "status": "running",
                             "kind": "tool", "step": 1})
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(conv.window(), "_trace_drawer_panel", None)
    assert drawer is not None
    assert drawer._detail.isHidden() is True

    conv.append_trace_event({"stage": "execute_sql", "title": "Calling 2", "status": "running",
                             "kind": "tool", "step": 2})
    qapp.processEvents()
    assert drawer._detail.isHidden() is True

    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[{
            "stage": "execute_sql",
            "title": "execute_sql done",
            "status": "completed",
            "kind": "tool",
            "step": 1,
            "sql": "SELECT COUNT(*) FROM orders",
            "duration_ms": 4,
        }],
        ok=True,
    )
    qapp.processEvents()
    assert drawer._detail.isHidden() is True
    conv.deleteLater()
    qapp.processEvents()


def test_show_trace_detail_no_fallback_panel_when_drawer_closed(qapp):
    from PyQt6.QtWidgets import QWidget
    from dbaide.desktop.components.trace import TraceDetailPanel, show_trace_detail

    host = QWidget()
    host.resize(900, 640)
    host.show()
    qapp.processEvents()
    show_trace_detail(host, {"title": "step", "stage": "execute_sql", "status": "running"})
    qapp.processEvents()
    panel = getattr(host.window(), "_trace_detail_panel", None)
    assert panel is None or not panel.isVisible()
    # Direct panel API still works for tests/tools.
    panel = TraceDetailPanel(host)
    panel.show_detail({"title": "step", "stage": "execute_sql", "status": "running"})
    assert panel.isVisible()
    panel.close_panel()
    host.deleteLater()
    qapp.processEvents()


def test_trace_drawer_card_height_stays_content_sized(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("count paid orders")
    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[{
            "stage": "execute_sql",
            "title": "execute_sql done",
            "status": "completed",
            "kind": "tool",
            "step": 1,
            "sql": "SELECT COUNT(*) FROM orders",
            "duration_ms": 4,
        }],
        ok=True,
    )
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    for _ in range(3):
        block._toggle_trace()
        qapp.processEvents()
        drawer = getattr(conv.window(), "_trace_drawer_panel", None)
        assert drawer is not None
        card = drawer._timeline._cards[0]
        assert card.height() <= card.sizeHint().height() + 4
        assert card.height() < 180
        block._toggle_trace()
        qapp.processEvents()
    conv.deleteLater()
    qapp.processEvents()


def test_switching_slots_closes_trace_drawer(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.set_has_connection(True)
    tab.begin_turn("s1", "count paid orders", connection="local", database="test")
    tab.append_result("s1", {
        "status": "completed",
        "answer_markdown": "3 paid orders.",
        "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                   "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM orders", "duration_ms": 4}],
    })
    tab.ensure_slot("s2")
    tab.set_active("s1")
    view1 = tab.view("s1")
    assert view1 is not None
    block = view1._layout.itemAt(view1._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(tab.window(), "_trace_drawer_panel", None)
    assert drawer is not None and drawer.isHidden() is False
    tab.set_active("s2")
    qapp.processEvents()
    assert drawer.isHidden() is True
    tab.deleteLater()
    qapp.processEvents()


def test_loading_session_closes_trace_drawer_before_replacing_turns(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.set_has_connection(True)
    tab.begin_turn("s1", "count paid orders", connection="local", database="test")
    tab.append_result("s1", {
        "status": "completed",
        "answer_markdown": "3 paid orders.",
        "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                   "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM orders", "duration_ms": 4}],
    })
    tab.set_active("s1")
    view = tab.view("s1")
    assert view is not None
    block = view._layout.itemAt(view._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(tab.window(), "_trace_drawer_panel", None)
    assert drawer is not None and drawer.isHidden() is False

    tab.load_session("s1", [{
        "question": "count refunded orders",
        "answer_markdown": "5 refunded orders.",
        "status": "completed",
        "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                   "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM refunds", "duration_ms": 5}],
    }], connection="local")
    qapp.processEvents()

    assert drawer.isHidden() is True
    view = tab.view("s1")
    assert view is not None
    new_block = view._layout.itemAt(view._layout.count() - 1).widget()
    new_block._toggle_trace()
    qapp.processEvents()
    assert drawer.isHidden() is False
    assert "SELECT COUNT(*) FROM refunds" in drawer._timeline.copy_text()
    tab.deleteLater()
    qapp.processEvents()


def test_clearing_slot_with_open_trace_closes_drawer(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.set_has_connection(True)
    tab.begin_turn("s1", "count paid orders", connection="local", database="test")
    tab.append_result("s1", {
        "status": "completed",
        "answer_markdown": "3 paid orders.",
        "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                   "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM orders", "duration_ms": 4}],
    })
    tab.set_active("s1")
    view = tab.view("s1")
    assert view is not None
    block = view._layout.itemAt(view._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(tab.window(), "_trace_drawer_panel", None)
    assert drawer is not None and drawer.isHidden() is False

    tab.clear_slot("s1")
    qapp.processEvents()
    assert drawer.isHidden() is True
    tab.deleteLater()
    qapp.processEvents()


def test_loading_other_slot_does_not_close_active_trace_drawer(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    tab.set_has_connection(True)
    for key, sql in (("s1", "SELECT 1"), ("s2", "SELECT 2")):
        tab.begin_turn(key, key, connection="local", database="test")
        tab.append_result(key, {
            "status": "completed",
            "answer_markdown": key,
            "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                       "kind": "tool", "step": 1, "sql": sql, "duration_ms": 4}],
        })

    tab.set_active("s1")
    view = tab.view("s1")
    assert view is not None
    block = view._layout.itemAt(view._layout.count() - 1).widget()
    block._toggle_trace()
    drawer = getattr(tab.window(), "_trace_drawer_panel", None)
    assert drawer is not None and drawer.isHidden() is False

    tab.load_session("s2", [{
        "question": "new-s2",
        "answer_markdown": "new-s2",
        "status": "completed",
        "trace": [{"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                   "kind": "tool", "step": 1, "sql": "SELECT 22", "duration_ms": 4}],
    }], connection="local")
    qapp.processEvents()

    assert drawer.isHidden() is False
    assert "SELECT 1" in drawer._timeline.copy_text()
    tab.deleteLater()
    qapp.processEvents()


def test_conversation_copy_exports_all_turns(qapp):
    from dbaide.desktop.components.conversation import ConversationView
    conv = ConversationView()
    # turn 1: a data query with a SQL trace
    conv.begin_turn("count paid orders")
    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[
            {"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
             "kind": "tool", "step": 1, "sql": "SELECT COUNT(*) FROM orders WHERE status='paid'",
             "row_count": 3, "duration_ms": 5},
        ],
        ok=True,
    )
    # turn 2: a schema question
    conv.begin_turn("what columns does orders have")
    conv.complete_turn(
        answer="id, amount, status.",
        trace_events=[{"stage": "discover_schema", "title": "discover_schema done",
                       "status": "completed", "kind": "tool", "step": 1, "detail": "1 hit"}],
        ok=True,
    )
    text = conv.copy_text()
    assert "### Turn 1" in text and "### Turn 2" in text
    assert "count paid orders" in text and "what columns does orders have" in text
    assert "SELECT COUNT(*) FROM orders" in text     # turn-1 SQL in trace
    assert "3 paid orders." in text and "id, amount, status." in text  # both answers
    conv.clear()
    assert conv.copy_text() == ""


def test_message_bubble_copy_actions_write_clipboard(qapp):
    from dbaide.desktop.components.conversation import _Bubble

    bubble = _Bubble("用户发的消息 abc", align_right=True)
    bubble.copy_message()
    assert QApplication.clipboard().text() == "用户发的消息 abc"

    bubble._label.setSelection(0, 2)
    bubble.copy_selection()
    assert QApplication.clipboard().text() == "用户"


def test_markdown_block_copy_action_writes_source_message(qapp):
    from dbaide.desktop.components.conversation import _MarkdownBlock

    markdown = "**Answer**\n\n```sql\nSELECT 1\n```"
    block = _MarkdownBlock(markdown, title="DBAide")
    block.copy_message()
    assert QApplication.clipboard().text() == markdown


def test_markdown_code_block_copy_button_copies_code_only(qapp):
    from dbaide.desktop.components.conversation import _MarkdownBlock

    markdown = "Before\n\n```sql\nSELECT 1;\n```\n\nAfter"
    block = _MarkdownBlock(markdown, title="DBAide")
    block.copy_first_code_block()
    assert QApplication.clipboard().text() == "SELECT 1;"


def test_markdown_code_block_handles_empty_and_code_only_messages(qapp):
    from dbaide.desktop.components.conversation import _MarkdownBlock

    QApplication.clipboard().clear()
    block = _MarkdownBlock("```text\n```")
    block.copy_first_code_block()
    assert QApplication.clipboard().text() == ""
    block.copy_message()  # must not crash


def test_markdown_code_blocks_update_during_streaming(qapp):
    from dbaide.desktop.components.conversation import _MarkdownBlock

    block = _MarkdownBlock("```sql\nSELECT 1\n```")
    block.set_markdown("Done\n\n```python\nprint(2)\n```")
    block.copy_first_code_block()
    assert QApplication.clipboard().text() == "print(2)"


def _wait_markdown_ready(block, qapp, *, timeout_ms: int = 500) -> None:
    import time

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if block._rendered is not None:
            return
        time.sleep(0.005)
    raise AssertionError("markdown block did not finish rendering")


def test_streaming_text_uses_plain_append_only_path(qapp):
    from dbaide.desktop.components.conversation import _MarkdownBlock

    block = _MarkdownBlock("", title="DBAide")
    block.set_streaming_text("hel")
    view = block._stream_view
    assert view is not None
    assert view.toPlainText() == "hel"
    block.set_streaming_text("hello")
    assert view.toPlainText() == "hello"
    block.set_markdown("**hello**", force_rebuild=True)
    _wait_markdown_ready(block, qapp)
    assert block._stream_view is None
    assert block._rendered is not None


def test_markdown_final_render_uses_web_widget(qapp, monkeypatch):
    import sys
    import types

    from PyQt6.QtCore import QTimer, pyqtSignal
    from PyQt6.QtWidgets import QWidget

    class _FakePage:
        def runJavaScript(self, _js, callback=None):
            if callback is not None:
                callback(120)

    class _FakeWebEngineView(QWidget):
        loadFinished = pyqtSignal(bool)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._html = ""

        def setHtml(self, html, _base_url=None):
            self._html = html
            QTimer.singleShot(0, lambda: self.loadFinished.emit(True))

        def page(self):
            return _FakePage()

    fake_module = types.ModuleType("PyQt6.QtWebEngineWidgets")
    fake_module.QWebEngineView = _FakeWebEngineView
    monkeypatch.setitem(sys.modules, "PyQt6.QtWebEngineWidgets", fake_module)

    from dbaide.desktop.components.conversation import _MarkdownBlock

    block = _MarkdownBlock("intro\n\n```sql\nSELECT 1\n```\n\noutro")
    _wait_markdown_ready(block, qapp)
    assert block._rendered is not None
    block.set_markdown("intro2\n\n```sql\nSELECT 2\n```\n\noutro2")
    _wait_markdown_ready(block, qapp)
    assert block._markdown.startswith("intro2")
    assert block._rendered is not None


def test_copy_answer_action_builds_menu_button(qapp):
    from PyQt6.QtWidgets import QToolButton
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    btn = tab._build_actions("There are **3** factories.", None)
    assert btn is not None
    assert isinstance(btn, QToolButton)


def test_answer_actions_none_when_empty(qapp):
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    assert tab._build_actions("", None) is None
    assert tab._build_actions("", "") is None


def test_sql_only_action_builds_menu_button(qapp):
    from PyQt6.QtWidgets import QToolButton
    from dbaide.desktop.views.ask_tab import AskTab

    tab = AskTab()
    btn = tab._build_actions("", None, "SELECT 1")
    assert btn is not None
    assert isinstance(btn, QToolButton)


def test_complete_turn_renders_answer_only(qapp):
    from dbaide.desktop.components.answer_document import AnswerDocumentBlock
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("q")
    conv.complete_turn(answer="Done.", ok=True)
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    answers = [
        w.markdown
        for i in range(block._content.count())
        if isinstance((w := block._content.itemAt(i).widget()), AnswerDocumentBlock)
    ]
    assert answers == ["Done."]


def test_md_inline_escape_preserves_error_text():
    from dbaide.desktop.components.conversation import _md_inline_escape
    from dbaide.rendering.markdown import render_markdown_safe

    msg = "near `orders`: col _id_ unknown *now*"
    html = render_markdown_safe("- " + _md_inline_escape(msg))
    # The DB error must render verbatim — no code span / emphasis from its punctuation.
    assert "<code>" not in html and "<em>" not in html
    assert "`orders`" in html and "_id_" in html


def test_complete_turn_error_note_is_escaped(qapp):
    from dbaide.desktop.components.conversation import ConversationView, _MarkdownBlock

    conv = ConversationView()
    conv.begin_turn("q")
    conv.complete_turn(
        answer="Done.",
        ok=False,
        errors=[{"stage": "execute_sql", "message": "bad col `a_b` near *x*"}],
    )
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    notes = [
        w._markdown
        for i in range(block._content.count())
        if isinstance((w := block._content.itemAt(i).widget()), _MarkdownBlock)
        and "execute" in getattr(w, "_markdown", "")
    ]
    # The error message's markdown punctuation is backslash-escaped in the note.
    assert notes and "\\`a\\_b\\`" in notes[0] and "\\*x\\*" in notes[0]


def test_answer_without_stream_renders_immediately(qapp):
    """No live chunks (model can't stream / streaming off) → the full answer renders at
    once, with no front-end simulation. The full text is stored for copy."""
    from dbaide.desktop.components.conversation import ConversationView
    long = "这是一个较长的回答,用于验证一次性整段渲染。" * 4
    v = ConversationView()
    v.begin_turn("q")
    v.complete_turn(answer=long, ok=True)
    assert v._live_answer is None                # no live block, no simulation
    assert v._turns[-1]["answer"] == long        # full text stored → copy unaffected
    assert long in v.copy_text()                 # complete answer rendered/exportable


def test_answer_chunks_stream_live_then_finalize(qapp):
    """True token-streaming: answer_chunk events fill a live block during the run;
    complete_turn snaps it to the authoritative text with no extra block."""
    from dbaide.desktop.components.conversation import ConversationView
    v = ConversationView()
    v.begin_turn("how many paid orders")
    v.append_answer_chunk("42 paid")
    assert v._live_answer is not None            # block created on first chunk
    v.append_answer_chunk(" orders")
    assert v._live_answer_text == "42 paid orders"
    v.complete_turn(answer="42 paid orders", ok=True)
    assert v._live_answer is None                # live state cleared
    assert v._turns[-1]["answer"] == "42 paid orders"  # full text stored for copy


def test_complete_turn_prefers_longer_stream_when_authoritative_is_prefix(qapp):
    """If live streaming ran ahead of partial JSON decode, keep the longer text."""
    from dbaide.desktop.components.conversation import ConversationView
    v = ConversationView()
    v.begin_turn("q")
    v.append_answer_chunk("hello world")
    v.complete_turn(answer="hello", ok=True)
    assert v._turns[-1]["answer"] == "hello world"


def test_conversation_tail_follow_pauses_on_scroll_up(qapp):
    """Streaming auto-scroll must pause when the user scrolls up to read, and resume
    when they return to the bottom — no scroll-jacking on every chunk."""
    from dbaide.desktop.components.conversation import ConversationView
    v = ConversationView()
    bar = v.verticalScrollBar()
    bar.setRange(0, 100)
    v._on_scroll_value(0)        # user scrolled to the top
    assert v._follow_bottom is False
    v._on_scroll_value(96)       # within slack of the bottom
    assert v._follow_bottom is True
    # A chunk flush while paused must still update the block (no crash) and not raise.
    v.begin_turn("q")
    v.append_answer_chunk("partial")
    v._follow_bottom = False
    v._flush_answer_chunk()
    assert v._live_answer_text == "partial"


def test_trace_events_do_not_scroll_when_user_is_reading_above(qapp, monkeypatch):
    from dbaide.desktop.components.conversation import ConversationView

    v = ConversationView()
    calls: list[str] = []
    monkeypatch.setattr(v, "_schedule_scroll_bottom", lambda: calls.append("scroll"))
    v.begin_turn("q")
    calls.clear()

    v._follow_bottom = False
    v.append_trace_event({"stage": "execute_sql", "title": "Calling", "status": "running", "kind": "tool"})
    assert calls == []

    v._follow_bottom = True
    v.append_trace_event({"stage": "execute_sql", "title": "Done", "status": "completed", "kind": "tool"})
    assert calls == ["scroll"]


def test_turn_footer_summary_matches_trace_drawer_summary(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("count paid orders")
    conv.complete_turn(
        answer="3 paid orders.",
        trace_events=[{
            "stage": "execute_sql",
            "title": "execute_sql done",
            "status": "completed",
            "kind": "tool",
            "step": 1,
            "sql": "SELECT COUNT(*) FROM orders",
            "duration_ms": 4,
            "prompt_tokens": 1200,
        }],
        ok=True,
    )
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    block._toggle_trace()
    qapp.processEvents()
    drawer = getattr(conv.window(), "_trace_drawer_panel", None)
    assert drawer is not None
    assert block._stats_label.text() == drawer._summary.text()


def test_finish_turn_error_clears_live_stream_state(qapp):
    """A mid-stream error must tear down the live-answer block + chunk timer, so the
    next turn's streamed answer doesn't render into the errored turn's block."""
    from dbaide.desktop.components.conversation import ConversationView
    v = ConversationView()
    v.begin_turn("q1")
    v.append_answer_chunk("partial ans")
    assert v._live_answer is not None
    v.finish_turn_error("connection dropped")
    assert v._live_answer is None and v._live_answer_text == ""
    assert not v._chunk_timer.isActive()
    # The next turn streams into a FRESH block, not the previous (errored) one.
    v.begin_turn("q2")
    v.append_answer_chunk("new answer")
    assert v._live_answer is not None and v._live_answer_text == "new answer"


def test_complete_turn_embeds_charts_inline(qapp):
    from dbaide.desktop.components.answer_document import AnswerDocumentBlock
    from dbaide.desktop.components.conversation import ConversationView

    chart = {
        "chart_id": "chart:1",
        "chart_type": "bar",
        "title": "Sales",
        "categories": ["A"],
        "series": [{"name": "n", "values": [1.0]}],
        "row_count": 1,
    }
    conv = ConversationView()
    conv.begin_turn("show chart")
    conv.complete_turn(
        answer="Before\n\n{{chart:1}}\n\nAfter",
        charts=[chart],
        ok=True,
    )
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    types = [
        w.__class__.__name__
        for i in range(block._content.count())
        if (w := block._content.itemAt(i).widget()) is not None
    ]
    assert types == ["AnswerDocumentBlock"]
    doc_block = block._content.itemAt(0).widget()
    assert isinstance(doc_block, AnswerDocumentBlock)
    assert "Before" in doc_block.markdown
    from dbaide.rendering.compose import compose_blocks
    composed = compose_blocks(doc_block._answer, doc_block._charts)
    assert [b["type"] for b in composed] == ["markdown", "chart", "markdown"]


def _bar_chart(cid: str):
    return {
        "chart_id": cid, "chart_type": "bar", "title": "Sales",
        "categories": ["A"], "series": [{"name": "n", "values": [1.0]}], "row_count": 1,
    }


def _block_types(block):
    return [
        w.__class__.__name__
        for i in range(block._content.count())
        if (w := block._content.itemAt(i).widget()) is not None
    ]


def test_complete_turn_appends_unreferenced_charts(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("show chart")
    conv.complete_turn(answer="Here is the breakdown.", charts=[_bar_chart("chart:1")], ok=True)
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    assert _block_types(block) == ["AnswerDocumentBlock"]
    doc_block = block._content.itemAt(0).widget()
    from dbaide.rendering.compose import compose_blocks
    composed = compose_blocks(doc_block._answer, doc_block._charts)
    assert [b["type"] for b in composed] == ["markdown", "chart"]


def test_complete_turn_chart_only_answer_renders_chart(qapp):
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("just the chart")
    conv.complete_turn(answer="", charts=[_bar_chart("chart:1")], ok=True)
    block = conv._layout.itemAt(conv._layout.count() - 1).widget()
    assert _block_types(block) == ["AnswerDocumentBlock"]
    doc_block = block._content.itemAt(0).widget()
    from dbaide.rendering.compose import compose_blocks
    composed = compose_blocks(doc_block._answer, doc_block._charts)
    assert composed and composed[0]["type"] == "chart"


def test_follow_at_bottom_tail_logic():
    from dbaide.desktop.components.trace import _follow_at_bottom

    # At/near the bottom → follow on.
    assert _follow_at_bottom(100, 100) is True
    assert _follow_at_bottom(95, 100) is True       # within slack
    assert _follow_at_bottom(84, 100, slack=20) is True
    # Scrolled up beyond slack → follow paused.
    assert _follow_at_bottom(10, 100) is False
    # Nothing to scroll (maximum 0) → at bottom → follow stays on.
    assert _follow_at_bottom(0, 0) is True


def test_trace_follow_pauses_and_resumes_on_scroll(qapp):
    from dbaide.desktop.components.trace import InlineTrace

    panel = InlineTrace()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="loop", title="started", status="running", kind="agent"))
    assert panel._state.follow_live is True

    bar = panel._tree.verticalScrollBar()
    # User scrolls up → follow pauses.
    bar.setMaximum(100)
    panel._on_user_scroll(0)
    assert panel._state.follow_live is False
    # User scrolls back to the bottom → follow resumes (tail behavior).
    panel._on_user_scroll(100)
    assert panel._state.follow_live is True


def test_trace_incremental_updates_reuse_step_cards(qapp):
    """Status/duration changes must patch existing cards, not destroy/recreate them."""
    from dbaide.desktop.components.trace import InlineTrace

    panel = InlineTrace()
    panel.begin_live()
    ev = progress_event(stage="execute_sql", title="Calling", status="running", kind="tool", step=1)
    panel.append_live_event(ev)
    panel._render_timer.stop()
    panel._render()
    assert len(panel._cards) == 1
    first_card = panel._cards[0]
    panel.append_live_event(
        progress_event(stage="execute_sql", title="done", status="completed", kind="tool", step=1, duration_ms=12)
    )
    panel._render()
    assert len(panel._cards) == 1
    assert panel._cards[0] is first_card


def test_trace_incremental_appends_new_step_cards(qapp):
    from dbaide.desktop.components.trace import InlineTrace

    panel = InlineTrace()
    panel.begin_live()
    panel.append_live_event(progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1))
    panel._render_timer.stop()
    panel._render()
    first_card = panel._cards[0]
    panel.append_live_event(progress_event(stage="execute_sql", title="Calling", status="running", kind="tool", step=2))
    panel._render()
    assert len(panel._cards) == 2
    assert panel._cards[0] is first_card


def test_config_stream_answers_default_on(tmp_path):
    from dbaide.config import ConfigManager
    cfg = ConfigManager(path=tmp_path / "config.toml")
    assert cfg.stream_answers() is True          # default on
    cfg.set_stream_answers(False)
    assert ConfigManager(path=tmp_path / "config.toml").stream_answers() is False  # persisted


def test_pill_select_set_value_validates_against_options(qapp):
    """A value not among the options (e.g. a stale model id) must fall back to a real
    option so value() is always selectable; with no options yet, the value is kept."""
    from dbaide.desktop.components.menu import PillSelect

    c = PillSelect()
    c.set_options([("GPT", "gpt"), ("Claude", "claude")])
    c.set_value("claude")
    assert c.value() == "claude"
    c.set_value("removed-model")          # not in options → fall back to first
    assert c.value() == "gpt"
    c2 = PillSelect()
    c2.set_value("pending")               # no options yet → keep (can't validate)
    assert c2.value() == "pending"


def test_clarification_bar_dismissed_on_error_and_new_turn(qapp):
    """A pending clarification bar must be retracted when its turn errors and when a
    new turn starts — it can't linger as a clickable prompt for a dead run."""
    from dbaide.desktop.components.conversation import ConversationView

    conv = ConversationView()
    conv.begin_turn("q1")
    bar = conv.append_clarification(question="Which date range?", options=["7d", "30d"])
    assert conv._clarification_bar is bar
    conv.finish_turn_error("connection dropped")
    assert conv._clarification_bar is None and bar.isHidden()

    # And a brand-new turn supersedes a still-showing bar.
    conv.begin_turn("q2")
    bar2 = conv.append_clarification(question="Which table?", options=["a", "b"])
    conv.begin_turn("q3")
    assert conv._clarification_bar is None and bar2.isHidden()


def test_code_block_update_code_syncs_language_label(qapp):
    """update_code must refresh the language label, not just the body."""
    from dbaide.desktop.components.conversation import _CodeBlock

    block = _CodeBlock("SELECT 1", language="sql")
    assert block._lang_label.text() == "SQL"
    block.update_code("print(1)", language="python")
    assert block._lang_label.text() == "PYTHON"
    block.update_code("x = 1", language="")          # no language → generic label
    assert block._lang_label.text() and block._lang_label.text() != "PYTHON"


def test_compact_controls_use_normalized_sizes(qapp):
    from dbaide.desktop.components.icon_button import IconToolButton
    from dbaide.desktop.components.icons import svg_icon
    from dbaide.desktop.components.menu import MenuButton, PillSelect
    from dbaide.desktop.components.composer import ComposerWidget
    from dbaide.desktop.views.sidebar import Sidebar
    from dbaide.desktop.theme import Theme

    icon_btn = IconToolButton(svg_icon("copy"), "Copy")
    assert icon_btn.size().width() == 26
    assert icon_btn.size().height() == 26
    assert icon_btn.iconSize().width() == 14
    assert icon_btn.iconSize().height() == 14

    menu_btn = MenuButton("More")
    assert menu_btn.height() == 28

    icon_menu = MenuButton(icon=svg_icon("more-horizontal"), icon_only=True, tooltip="More")
    assert icon_menu.size().width() == 28
    assert icon_menu.size().height() == 28

    pill = PillSelect()
    assert pill.height() == 28
    pill.set_options([("GPT-5", "gpt5")])
    pill.set_value("gpt5")
    assert pill.toolTip() == ""
    pill.set_options([("deepseek-r1-very-long-model-name", "deep")])
    pill.set_value("deep")
    assert pill.toolTip() == "deepseek-r1-very-long-model-name"

    sidebar = Sidebar()
    assert sidebar.search.height() == 28
    assert sidebar._split.handleWidth() == 4

    composer = ComposerWidget()
    assert _first_opaque_hex(composer.action_btn.icon(), 18) == Theme.ACCENT_TEXT.lower()


def test_normalize_selected_text_converts_both_separators():
    """Qt selections use U+2029 (paragraph) and U+2028 (line, from <br>) — both must
    become real newlines so copied text doesn't carry stray separator glyphs."""
    from dbaide.desktop.components.conversation import _normalize_selected_text

    s = "a" + chr(0x2029) + "b" + chr(0x2028) + "c"
    assert _normalize_selected_text(s) == "a\nb\nc"
    assert " " not in _normalize_selected_text(s)
    assert " " not in _normalize_selected_text(s)


def test_conversation_clear_resets_transient_state(qapp):
    """clear() (new/switched session) must reset streaming + follow state so stale
    flags from a prior session don't suppress auto-scroll or leak a live block."""
    from dbaide.desktop.components.conversation import ConversationView
    v = ConversationView()
    v.begin_turn("q")
    v.append_answer_chunk("partial")
    v._follow_bottom = False          # user had scrolled up
    v.clear()
    assert v._live_answer is None and v._live_answer_text == ""
    assert v._clarification_bar is None
    assert v._follow_bottom is True   # re-engaged for the fresh session
    assert not v._chunk_timer.isActive()


def test_svg_glyph_bytes_cached_renderer_fresh(qapp):
    """The formatted SVG bytes are cached by (name, color, width) to skip re-formatting,
    but each call gets a FRESH QSvgRenderer — caching the QObject would dangle across
    QApplication teardown. Distinct keys stay distinct."""
    from dbaide.desktop.components.icons import _glyph_svg_bytes, _renderer, svg_icon, _GLYPHS

    name = next(iter(_GLYPHS))
    assert _glyph_svg_bytes(name, "#ffffff", 2.0) is _glyph_svg_bytes(name, "#ffffff", 2.0)
    assert _glyph_svg_bytes(name, "#ffffff", 2.0) != _glyph_svg_bytes(name, "#000000", 2.0)
    # Renderers are fresh instances (not the cached-QObject pitfall).
    assert _renderer(name, "#fff", 2.0) is not _renderer(name, "#fff", 2.0)
    assert not svg_icon(name, color="#abcdef", size=16).isNull()


def test_full_rebuild_preserves_scroll_when_not_following(qapp):
    """A full rebuild (e.g. a step gaining its first sub-step) must not snap the view
    to the top when the user has scrolled up (follow_live off)."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from dbaide.desktop.components.trace import InlineTrace

    host = QWidget()
    host.resize(420, 300)
    host.show()
    trace = InlineTrace(host, show_header=False)
    lay = QVBoxLayout(host)
    lay.addWidget(trace)
    trace.show()
    qapp.processEvents()

    def flush():
        trace._render_timer.stop()
        trace._render()
        qapp.processEvents()

    # Tall content so the scroll area actually has range.
    events = [progress_event(stage="loop", title="started", status="running", kind="agent")]
    for i in range(20):
        events.append(progress_event(stage=f"s{i}", title=f"step {i} " + "x" * 40,
                                     status="completed", kind="tool", step=i + 1, duration_ms=3))
    trace.set_events(events, live=True)
    flush()

    bar = trace._scroll.verticalScrollBar()
    if bar.maximum() <= 0:
        import pytest
        pytest.skip("offscreen env produced no scroll range")

    trace._state.follow_live = False
    target = bar.maximum() // 2
    bar.setValue(target)
    qapp.processEvents()

    # Force a full rebuild by giving an existing childless step its first sub-step.
    events.append(subagent_event(agent="risk", title="auto", parent="s5",
                                 node_id="risk:x", status="completed"))
    trace.set_events(events, live=True)
    flush()

    # Scroll should be near where the user left it — NOT reset to the top.
    assert trace._scroll.verticalScrollBar().value() > 0
    assert abs(trace._scroll.verticalScrollBar().value() - target) <= 40
    host.deleteLater()
    qapp.processEvents()


def test_prefix_append_updates_previous_last_card_connector(qapp):
    """When new steps stream in via the prefix fast-path, the previously-last card must
    lose its is_last flag so the timeline connector to the new cards isn't dropped."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from dbaide.desktop.components.trace import InlineTrace

    host = QWidget()
    host.resize(420, 600)
    host.show()
    trace = InlineTrace(host, show_header=False)
    lay = QVBoxLayout(host)
    lay.addWidget(trace)
    trace.show()

    def flush():
        trace._render_timer.stop()
        trace._render()
        qapp.processEvents()

    events = [
        progress_event(stage="loop", title="started", status="running", kind="agent"),
        progress_event(stage="s1", title="step 1", status="completed", kind="tool", step=1, duration_ms=2),
    ]
    trace.set_events(events, live=True)
    flush()
    assert trace._cards[-1]._marker._is_last is True   # only card → is_last

    # Append a second top-level step via the prefix fast-path.
    events.append(progress_event(stage="s2", title="step 2", status="completed", kind="tool", step=2, duration_ms=2))
    trace.set_events(events, live=True)
    flush()

    assert len(trace._cards) == 2
    assert trace._cards[0]._marker._is_last is False   # old last no longer last (connector kept)
    assert trace._cards[-1]._marker._is_last is True
    host.deleteLater()
    qapp.processEvents()
