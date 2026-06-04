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


def test_append_clarification_multi_uses_stepper(qapp):
    from dbaide.desktop.components.conversation import (
        ConversationView,
        _ClarificationBar,
        _ClarificationStepper,
    )
    conv = ConversationView()
    conv.begin_turn("q")
    # Structured multi-question → a one-at-a-time stepper.
    bar = conv.append_clarification(
        question="Confirm:\n**1. Which timezone?**\n**2. Which status?**",
        options=["UTC"],
        questions=[
            {"ask": "Which timezone?", "options": ["UTC", "Asia/Shanghai"]},
            {"ask": "Which status?", "options": ["delivered", "returned"]},
        ],
    )
    assert isinstance(bar, _ClarificationStepper)
    # A single structured question → the direct bar (chips submit immediately).
    conv.begin_turn("q2")
    bar2 = conv.append_clarification(
        question="Which timezone?", options=[],
        questions=[{"ask": "Which timezone?", "options": ["UTC"]}],
    )
    assert isinstance(bar2, _ClarificationBar) and bar2._direct is True
    # No structured questions at all → still a usable direct bar.
    conv.begin_turn("q3")
    bar3 = conv.append_clarification(question="Which timezone?", options=[])
    assert isinstance(bar3, _ClarificationBar) and bar3._direct is True


def test_clarification_stepper_steps_and_assembles(qapp):
    from dbaide.desktop.components.conversation import _ClarificationStepper
    stepper = _ClarificationStepper([
        {"ask": "Which timezone?", "options": ["UTC", "Asia/Shanghai"]},
        {"ask": "Which status?", "options": ["delivered", "returned"]},
    ])
    got = []
    stepper.submitted.connect(got.append)
    # First question: picking a chip advances without submitting yet.
    assert stepper._idx == 0
    stepper._answer("Asia/Shanghai")
    assert got == [] and stepper._idx == 1               # advanced, not submitted
    # Last question: answering assembles a numbered reply and submits.
    stepper._answer("delivered")
    assert got == ["1. Asia/Shanghai\n2. delivered"]


def test_clarification_stepper_back_preserves_answer(qapp):
    from dbaide.desktop.components.conversation import _ClarificationStepper
    stepper = _ClarificationStepper([
        {"ask": "Which timezone?", "options": ["UTC"]},
        {"ask": "Which status?", "options": ["delivered"]},
    ])
    stepper._input.setText("UTC")
    stepper._on_next()
    assert stepper._idx == 1
    stepper._on_back()
    assert stepper._idx == 0 and stepper._input.text() == "UTC"  # answer restored


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


class _FakeWorker:
    is_cancelled = False
    def cancel(self):
        self.is_cancelled = True


def _make_window(tmp_path):
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
    return MainWindow(DesktopService(cfg, AssetStore(tmp_path / "assets")))


def _arm_clarification(win, key="sessA"):
    """Put the active slot into a wait-for-reply state (a clarification is pending)."""
    win.ask_tab.ensure_slot(key)
    win._active_key = key
    win.ask_tab.set_active(key)
    win._slot_session[key] = key
    win._pending_resume[key] = {"question": "count paid orders by city"}
    win._slot_question[key] = "count paid orders by city"
    return key


def test_clarification_reply_queues_when_at_cap(qapp, tmp_path):
    """A clarification reply submitted while every run slot is busy must be QUEUED,
    never lost — it starts automatically when a slot frees."""
    win = _make_window(tmp_path)
    _drain(qapp)
    key = _arm_clarification(win)

    # Saturate the run cap with another (fake) in-flight run.
    win._max_runs = 1
    win._runs["other"] = _FakeWorker()

    win._submit_clarification(key, "UTC")

    # The reply became a queued run for this slot (not dropped).
    assert any(k == key for k, _ in win._run_queue), "reply was lost instead of queued"
    assert key not in win._pending_resume, "pause should be consumed once the reply is in the queue"

    win.deleteLater()
    qapp.processEvents()


def test_clarification_reply_launches_when_idle(qapp, tmp_path):
    """With a free slot, the reply launches the resume run immediately."""
    win = _make_window(tmp_path)
    _drain(qapp)
    key = _arm_clarification(win)

    win._submit_clarification(key, "UTC")
    # A worker was launched for this slot (resume in flight), pause consumed.
    assert key in win._runs, "idle reply should launch the resume"
    assert key not in win._pending_resume

    _drain(qapp)  # let it finish so it doesn't outlive the test
    win.deleteLater()
    qapp.processEvents()
