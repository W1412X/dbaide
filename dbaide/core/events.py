"""Trace event data structures for DBAide workflow engine."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any


class TraceLevel(str, Enum):
    """Trace event severity level."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TraceKind(str, Enum):
    """Trace event kind."""
    AGENT = "agent"
    TOOL = "tool"
    VALIDATION = "validation"
    EXECUTION = "execution"
    USER = "user"
    SYSTEM = "system"


class TraceEvent:
    """Single trace event in a workflow execution.

    Used by GUI trace display, JSON output and debug bundles.
    """

    __slots__ = (
        "event_id", "workflow_id", "parent_event_id",
        "timestamp", "level", "kind", "stage",
        "actor", "title", "summary",
        "input_preview", "output_preview",
        "duration_ms", "status", "metadata",
    )

    def __init__(
        self,
        *,
        event_id: str = "",
        workflow_id: str = "",
        parent_event_id: str = "",
        timestamp: float = 0.0,
        level: TraceLevel = TraceLevel.INFO,
        kind: TraceKind = TraceKind.SYSTEM,
        stage: str = "",
        actor: str = "",
        title: str = "",
        summary: str = "",
        input_preview: str = "",
        output_preview: str = "",
        duration_ms: float = 0.0,
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.event_id = event_id or str(uuid.uuid4())[:8]
        self.workflow_id = workflow_id
        self.parent_event_id = parent_event_id
        self.timestamp = timestamp
        self.level = level
        self.kind = kind
        self.stage = stage
        self.actor = actor
        self.title = title
        self.summary = summary
        self.input_preview = input_preview
        self.output_preview = output_preview
        self.duration_ms = duration_ms
        self.status = status
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "parent_event_id": self.parent_event_id,
            "timestamp": self.timestamp,
            "level": self.level.value,
            "kind": self.kind.value,
            "stage": self.stage,
            "actor": self.actor,
            "title": self.title,
            "summary": self.summary,
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        return f"TraceEvent({self.stage}: {self.title})"
