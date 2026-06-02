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
