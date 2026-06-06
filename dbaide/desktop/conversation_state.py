from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ThinkingUiState:
    """UI state for a single turn's thinking / trace chip."""

    running: bool = False
    waiting: bool = False
    phase: str = "Thinking..."
    ok: bool = True
    step_count: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    expanded: bool = False

    def start(self, phase: str = "Thinking...") -> None:
        self.running = True
        self.waiting = False
        if phase:
            self.phase = phase

    def set_phase(self, phase: str) -> None:
        if not phase:
            return
        self.running = True
        self.waiting = False
        self.phase = phase if len(phase) <= 60 else phase[:59] + "..."

    def set_waiting(self, text: str = "Waiting for your reply...") -> None:
        self.running = False
        self.waiting = True
        self.phase = text

    def set_done(self, *, ok: bool, step_count: int, events: list[dict[str, Any]]) -> None:
        self.running = False
        self.waiting = False
        self.ok = ok
        self.step_count = max(0, int(step_count))
        self.events = list(events or [])

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)


@dataclass
class TurnTraceState:
    """Trace lifecycle for one conversation turn."""

    events: list[dict[str, Any]] = field(default_factory=list)
    final: bool = False

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def set_final(self, events: list[dict[str, Any]]) -> None:
        self.events = list(events or [])
        self.final = True

    def reset(self) -> None:
        self.events.clear()
        self.final = False
