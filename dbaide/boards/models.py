"""Data models for saved questions and dashboards.

A ``SavedQuestion`` is the re-runnable unit captured when the user pins a chart
from an answer. It carries everything needed to redraw the chart from fresh data
*without* calling the model again:

- ``sql`` — the query that produced the chart's rows (deterministic re-run)
- ``chart_plan`` — the field→role mapping (chart type, category/value fields…)
  so fresh rows can be re-materialised into a chart spec
- ``chart_spec`` / ``columns`` / ``row_count`` — a snapshot of the last result,
  shown instantly when a board opens (refresh happens on demand)

A question with no ``sql``/``chart_plan`` is still valid — it just renders its
snapshot statically and reports ``refreshable == False``.

A ``Dashboard`` is an ordered set of ``Tile``s; each tile references a saved
question by id (single source of truth) plus its position in a 12-column grid.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

GRID_COLUMNS = 12


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in known}


@dataclass
class SavedQuestion:
    """A pinned, re-runnable question + its chart."""

    name: str
    connection_name: str
    id: str = field(default_factory=new_id)
    nl_question: str = ""
    sql: str = ""
    database: str = ""
    chart_plan: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_run_at: str = ""

    @property
    def refreshable(self) -> bool:
        """Can this tile be re-run deterministically (SQL + chart plan present)?"""
        return bool(str(self.sql).strip() and self.chart_plan)

    def to_dict(self) -> dict[str, Any]:
        # include the derived `refreshable` flag for the UI; from_dict ignores it
        return {**asdict(self), "refreshable": self.refreshable}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavedQuestion":
        return cls(**_filter_known(cls, data))


@dataclass
class Tile:
    """A reference to a saved question and its slot in the board grid."""

    question_id: str
    x: int = 0
    y: int = 0
    w: int = 6
    h: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tile":
        d = _filter_known(cls, data)
        d["question_id"] = str(d.get("question_id") or "")
        for k in ("x", "y", "w", "h"):
            if k in d:
                try:
                    d[k] = int(d[k])
                except (TypeError, ValueError):
                    d.pop(k)
        return cls(**d)


@dataclass
class Dashboard:
    """An ordered grid of tiles."""

    name: str
    id: str = field(default_factory=new_id)
    tiles: list[Tile] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tiles"] = [t.to_dict() if isinstance(t, Tile) else dict(t) for t in self.tiles]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Dashboard":
        d = _filter_known(cls, data)
        d["tiles"] = [
            Tile.from_dict(t) for t in (data.get("tiles") or []) if isinstance(t, dict)
        ]
        return cls(**d)

    def next_slot(self, *, w: int = 6, h: int = 5) -> tuple[int, int]:
        """Place a new tile below the lowest existing one in column 0."""
        if not self.tiles:
            return (0, 0)
        bottom = max((t.y + t.h) for t in self.tiles)
        return (0, bottom)
