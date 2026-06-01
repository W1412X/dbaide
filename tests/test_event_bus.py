"""Tests for the desktop EventBus pub/sub."""

from __future__ import annotations

import pytest

from dbaide.desktop.event_bus import ASSETS_CHANGED, EventBus


def test_subscribe_and_emit_delivers_payload():
    bus = EventBus()
    seen = []
    bus.subscribe(ASSETS_CHANGED, lambda p: seen.append(p))
    bus.emit(ASSETS_CHANGED, {"instance": "prod"})
    assert seen == [{"instance": "prod"}]


def test_multiple_subscribers_all_notified():
    bus = EventBus()
    calls = []
    bus.subscribe("x", lambda p: calls.append("a"))
    bus.subscribe("x", lambda p: calls.append("b"))
    bus.emit("x")
    assert calls == ["a", "b"]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    unsub = bus.subscribe("x", lambda p: seen.append(p))
    bus.emit("x", 1)
    unsub()
    bus.emit("x", 2)
    assert seen == [1]


def test_failing_subscriber_does_not_block_others():
    bus = EventBus()
    seen = []
    bus.subscribe("x", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("x", lambda p: seen.append("ok"))
    bus.emit("x")
    assert seen == ["ok"]


def test_emit_with_no_subscribers_is_noop():
    EventBus().emit("nobody-home", 42)  # must not raise


def test_subscriber_count_and_clear():
    bus = EventBus()
    bus.subscribe("x", lambda p: None)
    bus.subscribe("x", lambda p: None)
    assert bus.subscriber_count("x") == 2
    bus.clear()
    assert bus.subscriber_count("x") == 0
