"""Trace drawer overlay — unified state, lifecycle, and window controller."""

from dbaide.desktop.trace.helpers import (
    alive_widget,
    clamp_drawer_geometry,
    follow_at_bottom,
    is_descendant,
    timeline_fingerprint,
)
from dbaide.desktop.trace.overlay import (
    TraceOverlayController,
    close_trace_overlays,
    close_trace_overlays_for,
    show_trace_detail,
    toggle_trace_drawer,
    update_trace_drawer,
)
from dbaide.desktop.trace.session import TraceViewState

__all__ = [
    "TraceOverlayController",
    "TraceViewState",
    "alive_widget",
    "clamp_drawer_geometry",
    "close_trace_overlays",
    "close_trace_overlays_for",
    "follow_at_bottom",
    "is_descendant",
    "show_trace_detail",
    "timeline_fingerprint",
    "toggle_trace_drawer",
    "update_trace_drawer",
]
