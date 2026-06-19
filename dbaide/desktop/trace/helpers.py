"""Shared helpers for trace UI widgets (no Qt widgets besides types)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from dbaide.agent.trace_model import TraceTimelineEntry


def alive_widget(obj: object, cls: type[QWidget] | None = None) -> QWidget | None:
    if obj is None:
        return None
    if cls is not None and not isinstance(obj, cls):
        return None
    try:
        if sip.isdeleted(obj):
            return None
    except RuntimeError:
        return None
    return obj  # type: ignore[return-value]


def is_descendant(widget: QWidget | None, ancestor: QWidget) -> bool:
    probe = alive_widget(widget)
    target = alive_widget(ancestor)
    while probe is not None and target is not None:
        if probe is target:
            return True
        probe = alive_widget(probe.parentWidget())
    return False


def follow_at_bottom(value: int, maximum: int, *, slack: int = 8) -> bool:
    return value >= maximum - slack


def timeline_fingerprint(timeline: list[TraceTimelineEntry]) -> tuple[tuple, ...]:
    """Stable signature for incremental timeline sync."""

    def _fp(entry: TraceTimelineEntry) -> tuple:
        return (
            entry.node_id,
            entry.status,
            entry.title,
            entry.summary,
            int(entry.duration_ms or 0),
            entry.step,
            entry.depth,
            tuple((c.node_id, c.status, c.title) for c in entry.children),
        )

    return tuple(_fp(entry) for entry in timeline)


def clamp_drawer_geometry(host_w: int, host_h: int, drawer_w: int) -> tuple[int, int, int, int]:
    host_w = max(1, int(host_w))
    host_h = max(1, int(host_h))
    w = min(max(320, int(drawer_w)), host_w)
    x = max(0, host_w - w)
    return x, 0, w, host_h
