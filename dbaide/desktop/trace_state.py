from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbaide.agent.trace_model import TraceModel


@dataclass
class InlineTraceState:
    """Non-widget state for the inline trace tree."""

    model: TraceModel | None = None
    selected_id: str = ""

    def set_events(self, events: list[dict[str, Any]], *, live: bool = False) -> TraceModel:
        model = TraceModel()
        for event in events or []:
            model.ingest(event)
        if not live:
            model.finalize()
        self.model = model
        self.selected_id = ""
        return model

    def begin_live(self) -> TraceModel:
        self.model = TraceModel()
        self.selected_id = ""
        return self.model

    def append_live_event(self, event: dict[str, Any]) -> None:
        if self.model is None:
            self.begin_live()
        assert self.model is not None
        self.model.ingest(event)

    def end_live(self) -> None:
        if self.model is not None:
            self.model.finalize()

    def clear(self) -> None:
        self.model = None
        self.selected_id = ""

    def is_empty(self) -> bool:
        return self.model is None or not self.model.steps
