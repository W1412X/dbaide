"""Unified trace view state — single source of truth for drawer + timeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from dbaide.agent.trace_model import TraceModel, localized_summary_line


@dataclass
class TraceViewState:
    """Presentation state for one open trace drawer session."""

    owner_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    live: bool = False
    ok: bool = True
    expanded_node_ids: set[str] = field(default_factory=set)
    follow_live: bool = True
    detail_pinned: bool = False
    detail_payload: dict[str, Any] | None = None
    selected_id: str = ""
    on_close: Callable[[], None] | None = None
    _model: TraceModel | None = field(default=None, repr=False)
    _timeline_fp: tuple[tuple, ...] = field(default_factory=tuple, repr=False)

    def clear(self) -> None:
        self.owner_id = ""
        self.events = []
        self.live = False
        self.ok = True
        self.expanded_node_ids.clear()
        self.follow_live = True
        self.detail_pinned = False
        self.detail_payload = None
        self.selected_id = ""
        self.on_close = None
        self._model = None
        self._timeline_fp = ()

    @property
    def model(self) -> TraceModel | None:
        return self._model

    def summary_line(self) -> str:
        if self._model is None:
            return ""
        return localized_summary_line(self._model)

    def load(
        self,
        *,
        owner_id: str,
        events: list[dict[str, Any]],
        live: bool,
        ok: bool,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.owner_id = str(owner_id or "")
        self.on_close = on_close
        self.apply_events(events, live=live, ok=ok)

    def apply_events(
        self,
        events: list[dict[str, Any]],
        *,
        live: bool,
        ok: bool,
    ) -> bool:
        """Rebuild the trace model. Returns True when event list changed."""
        new_events = list(events or [])
        changed = new_events != self.events or live != self.live or ok != self.ok
        self.events = new_events
        self.live = bool(live)
        self.ok = bool(ok)
        model = TraceModel()
        for event in self.events:
            model.ingest(event)
        if not self.live:
            model.finalize()
        self._model = model
        return changed

    def set_timeline_fingerprint(self, fp: tuple[tuple, ...]) -> None:
        self._timeline_fp = fp

    def timeline_fingerprint(self) -> tuple[tuple, ...]:
        return self._timeline_fp

    def pin_detail(self, payload: dict[str, Any]) -> None:
        self.detail_pinned = True
        self.detail_payload = dict(payload)

    def unpin_detail(self) -> None:
        self.detail_pinned = False
        self.detail_payload = None

    def notify_closed(self) -> None:
        callback = self.on_close
        self.on_close = None
        if callable(callback):
            callback()

    # ── Timeline widget API (used by InlineTrace + tests) ─────────────────

    def set_events(self, events: list[dict[str, Any]], *, live: bool = False) -> TraceModel:
        self.apply_events(events, live=live, ok=self.ok)
        self.selected_id = ""
        return self._model  # type: ignore[return-value]

    def begin_live(self) -> TraceModel:
        self.events = []
        self.live = True
        self._model = TraceModel()
        self.selected_id = ""
        return self._model

    def append_live_event(self, event: dict[str, Any]) -> None:
        if self._model is None:
            self.begin_live()
        if self._model is None:
            return
        self.events.append(event)
        self._model.ingest(event)

    def end_live(self) -> None:
        if self._model is not None:
            self._model.finalize()
        self.live = False

    def is_empty(self) -> bool:
        return self._model is None or not self._model.steps
