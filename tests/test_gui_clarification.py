"""Clarification reply controls: full-text chips, an inline input+Send, and the
multi-question fix (a chip fills the input instead of submitting one answer)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_single_question_chip_submits_directly(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar(["UTC", "America/New_York"], allow_direct_submit=True)
    got = []
    bar.submitted.connect(got.append)
    bar._on_chip("America/New_York")
    assert got == ["America/New_York"]


def test_multi_question_chip_fills_input_not_submit(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar(["delivered", "returned"], allow_direct_submit=False)
    got = []
    bar.submitted.connect(got.append)
    bar._on_chip("delivered")
    bar._on_chip("returned")
    assert got == []                                  # no premature submit
    assert bar._input.text() == "delivered; returned"  # answers accumulate
    bar._on_send()
    assert got == ["delivered; returned"]              # sent together


def test_typed_reply_submits(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar([], allow_direct_submit=True)   # open question, no chips
    got = []
    bar.submitted.connect(got.append)
    bar._input.setText("use America/New_York; only delivered")
    bar._on_send()
    assert got == ["use America/New_York; only delivered"]
    # empty input does not submit
    bar2 = _ClarificationBar([], allow_direct_submit=True)
    fired = []
    bar2.submitted.connect(fired.append)
    bar2._on_send()
    assert fired == []


def test_append_clarification_detects_multi_and_always_has_input(qapp):
    from dbaide.desktop.components.conversation import ConversationView
    conv = ConversationView()
    conv.begin_turn("q")
    multi_q = "Confirm:\n**1. Which timezone?**\n**2. Which status?**"
    bar = conv.append_clarification(question=multi_q, options=["UTC"])
    assert bar is not None and bar._direct is False        # multi → chips fill input
    # open question (no options) still yields a usable input bar
    conv.begin_turn("q2")
    bar2 = conv.append_clarification(question="**1. Which timezone?**", options=[])
    assert bar2 is not None and bar2._direct is True


def _drain(qapp):
    from PyQt6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(3000)
    for _ in range(8):
        qapp.processEvents()


def _wait_user_result():
    return {
        "status": "wait_user",
        "resume_state": {"question": "count paid orders by city"},
        "question": "count paid orders by city",
        "pending_question": "Which timezone should the day boundary use?",
        "pending_options": ["UTC", "Asia/Shanghai"],
        "trace": [],
        "session_id": "",
    }


def test_clarification_reply_not_lost_when_another_action_in_flight(qapp, tmp_path):
    """Regression: replying to a clarification while another worker (e.g. a schema
    preview / search / refresh) is still in flight must NOT consume the pause state.

    Previously _submit_clarification hid the bar and cleared _pending_resume *before*
    run_action's busy-guard rejected the resume — losing the reply and stranding the
    user with no options to click and no way to resume. The reply must survive so the
    user can submit again once the other action finishes.
    """
    import sqlite3
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))

    win = MainWindow(service)
    _drain(qapp)  # bootstrap selects the connection

    # The agent paused for a clarification → the bar is shown, _pending_resume is set.
    win.handle_result("ask", _wait_user_result())
    assert win._pending_resume is not None
    assert win.ask_tab.conversation._clarification_bar is not None
    bar = win.ask_tab.conversation._clarification_bar

    # Now another action is in flight (a stray preview/search/refresh worker).
    win.running = True
    win._current_worker = object()

    # The user clicks an option chip → submits the reply.
    win._submit_clarification("UTC")

    # The pause state and the bar must be preserved (reply not lost), so the user can
    # answer again once the other action finishes — NOT stranded with no options.
    assert win._pending_resume is not None, "resume state was wiped while busy — reply lost"
    assert win.ask_tab.conversation._clarification_bar is bar, "bar was hidden while busy"

    win.deleteLater()
    qapp.processEvents()


def test_clarification_reply_proceeds_when_idle(qapp, tmp_path):
    """Happy path: with no other action in flight, submitting a clarification reply
    consumes the pause state and starts the resume."""
    import sqlite3
    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow
    from dbaide.models import ConnectionConfig

    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);")
    c.commit(); c.close()
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))

    win = MainWindow(service)
    _drain(qapp)

    win.handle_result("ask", _wait_user_result())
    assert win._pending_resume is not None

    # Idle (no worker) → the reply is accepted and the pause state is consumed.
    win.running = False
    win._current_worker = None
    win._submit_clarification("UTC")
    assert win._pending_resume is None, "idle reply should consume the pause state and resume"

    _drain(qapp)  # let the resume worker finish so it doesn't outlive the test
    win.deleteLater()
    qapp.processEvents()
