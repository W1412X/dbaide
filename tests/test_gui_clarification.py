"""Clarification reply controls: wrapped option rows, an inline input+Send, and
the multi-question fix (an option fills the input instead of submitting one answer)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QSizePolicy  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_single_question_option_submits_directly(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar(["UTC", "America/New_York"], allow_direct_submit=True)
    got = []
    bar.submitted.connect(got.append)
    bar._on_chip("America/New_York")
    assert got == ["America/New_York"]


def test_multi_question_option_fills_input_not_submit(qapp):
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


def test_long_clarification_options_are_full_width_wrapped_rows(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    long_option = (
        "Use the completed delivery timestamp from order_data.delivery_detail, "
        "but only when refund_status is empty and the parcel state is delivered"
    )
    bar = _ClarificationBar([long_option], allow_direct_submit=True)
    row = bar._option_rows[0]
    assert row.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Preferred
    assert row.maximumHeight() > 1000
    assert row.label.wordWrap() is True
    assert row.label.text() == long_option


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
    # First question: picking an option advances without submitting yet.
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


def test_clarification_stepper_back_preserves_unsaved_typed_input(qapp):
    """Typing an answer then clicking Back (without Next) must not lose the input —
    coming forward again restores it."""
    from dbaide.desktop.components.conversation import _ClarificationStepper
    stepper = _ClarificationStepper([
        {"ask": "Which timezone?", "options": ["UTC"]},
        {"ask": "Which status?", "options": ["delivered"]},
    ])
    stepper._input.setText("UTC")
    stepper._on_next()                      # advance to Q2 (UTC recorded)
    assert stepper._idx == 1
    stepper._input.setText("shipped")       # type into Q2 but DON'T press Next
    stepper._on_back()                      # go back to Q1
    assert stepper._idx == 0
    stepper._on_next()                      # forward to Q2 again
    assert stepper._idx == 1
    assert stepper._input.text() == "shipped"   # in-progress input preserved


def test_bar_double_submit_emits_once(qapp):
    """A fast double-click/Enter must not emit two replies (the 2nd would be lost)."""
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar([], allow_direct_submit=True)
    got = []
    bar.submitted.connect(got.append)
    bar._input.setText("only delivered")
    bar._on_send()
    bar._on_send()  # second click before the bar is removed
    assert got == ["only delivered"]
    assert not bar._send.isEnabled() and not bar._input.isEnabled()


def test_bar_double_chip_emits_once(qapp):
    from dbaide.desktop.components.conversation import _ClarificationBar
    bar = _ClarificationBar(["UTC", "America/New_York"], allow_direct_submit=True)
    got = []
    bar.submitted.connect(got.append)
    bar._on_chip("UTC")
    bar._on_chip("America/New_York")  # second chip click — ignored
    assert got == ["UTC"]


def test_stepper_double_finish_emits_once(qapp):
    from dbaide.desktop.components.conversation import _ClarificationStepper
    stepper = _ClarificationStepper([
        {"ask": "Which timezone?", "options": ["UTC", "Asia/Shanghai"]},
    ])
    got = []
    stepper.submitted.connect(got.append)
    stepper._answer("UTC")        # last question → finish + submit
    stepper._on_next()            # a stray Enter after finish — ignored
    assert got == ["1. UTC"]
    assert not stepper._next.isEnabled()


