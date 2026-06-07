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
    # Summary row + 2 step rows.
    assert tree.topLevelItemCount() == 3
    assert not panel.is_empty()
    # The first tool step has two parallel sub-agent siblings (the two db scans).
    step1 = tree.topLevelItem(1)
    assert step1.childCount() == 2
    assert step1.isExpanded() is False


def test_trace_detail_dialog_shows_step(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    from dbaide.desktop.components.trace import InlineTrace, TraceDetailPanel

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
    monkeypatch.setattr(message_dialog, "confirm", lambda *a, **k: True)
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


def test_turn_inline_trace_toggles(qapp):
    """Clicking a completed turn's chip expands an inline trace; clicking again hides
    it. The trace is built lazily (no InlineTrace until first expand)."""
    from dbaide.desktop.components.conversation import ConversationView
    from dbaide.desktop.components.trace import InlineTrace

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
    assert block._trace_box is None             # lazy — not built until expanded
    block._toggle_trace()                        # expand
    assert isinstance(block._trace_box, InlineTrace)
    assert block._trace_box.isHidden() is False
    assert not block._trace_box.is_empty()
    assert block.status._expanded is True
    block._toggle_trace()                        # collapse
    assert block._trace_box.isHidden() is True
    assert block.status._expanded is False


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


def test_config_stream_answers_default_on(tmp_path):
    from dbaide.config import ConfigManager
    cfg = ConfigManager(path=tmp_path / "config.toml")
    assert cfg.stream_answers() is True          # default on
    cfg.set_stream_answers(False)
    assert ConfigManager(path=tmp_path / "config.toml").stream_answers() is False  # persisted
