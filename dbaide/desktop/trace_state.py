"""Backward-compatible re-export — prefer ``dbaide.desktop.trace.session``."""

from dbaide.desktop.trace.session import TraceViewState as InlineTraceState

__all__ = ["InlineTraceState"]
