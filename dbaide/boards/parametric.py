"""Recipe model for an AI-compiled, parameterized chart.

The LLM is a *compiler*, not an interpreter: once, at generation time, it turns a
chart's query logic into this declarative recipe — one or more SQL templates with
``:param`` placeholders, a declarative rule for combining their result sets, the
chart plan (field→role mapping, reused as-is), and the parameter schema. At
interaction time the recipe runs in pure Python (bind params → run SQLs →
combine → materialize) with **no** model call, so a control change is fast,
cheap, deterministic, and safe.

This module is the data model only; :mod:`dbaide.boards.runtime` executes it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from dbaide.boards.models import new_id, utc_now

PARAM_TYPES = ("text", "number", "date", "enum")
COMBINE_MODES = ("single", "union", "join")


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in known}


@dataclass
class ParamSpec:
    """A control on the dashboard: a typed, optionally-constrained parameter.

    ``default`` may be a dynamic token (e.g. ``"@today"``, ``"@month_start"``,
    ``"@days_ago:30"``) resolved at run time. ``multi=True`` (for ``enum``) makes
    the value a list that binds as a comma-list for ``IN (:name)``. A range filter
    is modelled as two single params (start/end) and one range control in the UI.
    """

    name: str                       # placeholder used in SQL as :name
    type: str = "text"              # text | number | date | enum
    label: str = ""
    default: Any = None
    options: list[Any] = field(default_factory=list)   # allowed values for type=enum
    multi: bool = False             # enum multi-select → IN (...) list

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ParamSpec":
        out = cls(**_filter_known(cls, d))
        out.type = out.type if out.type in PARAM_TYPES else "text"
        return out


@dataclass
class QuerySource:
    """One read-only SQL template feeding the chart; ``sql`` holds :param tokens."""

    id: str
    sql: str
    label: str = ""                 # used as the source tag in a union combine

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QuerySource":
        return cls(**_filter_known(cls, d))


@dataclass
class Combine:
    """How multiple source result sets become the single row set the plan consumes."""

    mode: str = "single"            # single | union | join
    key: str = ""                   # join column (mode=join)
    tag_field: str = ""             # column to hold each source's label (mode=union)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Combine":
        out = cls(**_filter_known(cls, d))
        out.mode = out.mode if out.mode in COMBINE_MODES else "single"
        return out


@dataclass
class ParametricChart:
    chart_id: str
    title: str = ""
    sources: list[QuerySource] = field(default_factory=list)
    params: list[ParamSpec] = field(default_factory=list)
    combine: Combine = field(default_factory=Combine)
    chart_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "title": self.title,
            "sources": [s.to_dict() for s in self.sources],
            "params": [p.to_dict() for p in self.params],
            "combine": self.combine.to_dict(),
            "chart_plan": dict(self.chart_plan),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ParametricChart":
        return cls(
            chart_id=str((d or {}).get("chart_id") or ""),
            title=str((d or {}).get("title") or ""),
            sources=[QuerySource.from_dict(s) for s in (d.get("sources") or []) if isinstance(s, dict)],
            params=[ParamSpec.from_dict(p) for p in (d.get("params") or []) if isinstance(p, dict)],
            combine=Combine.from_dict(d.get("combine") if isinstance(d.get("combine"), dict) else {}),
            chart_plan=dict(d.get("chart_plan") or {}),
        )

    def default_params(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}


@dataclass
class ParametricDashboard:
    """An AI-compiled interactive dashboard: shared controls drive several charts.

    Controls are the de-duplicated union of every chart's params (same-named
    params across charts share one control), so changing the top filter bar
    re-runs all charts together — like a real BI board.
    """

    name: str
    connection_name: str
    id: str = field(default_factory=new_id)
    charts: list[ParametricChart] = field(default_factory=list)
    layout: list[dict[str, Any]] = field(default_factory=list)   # [{chart_id, x, y, w, h}]
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def controls(self) -> list[ParamSpec]:
        seen: dict[str, ParamSpec] = {}
        out: list[ParamSpec] = []
        for chart in self.charts:
            for p in chart.params:
                if p.name not in seen:
                    seen[p.name] = p
                    out.append(p)
        return out

    def default_params(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.controls()}

    def chart(self, chart_id: str) -> ParametricChart | None:
        return next((c for c in self.charts if c.chart_id == chart_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "connection_name": self.connection_name,
            "charts": [c.to_dict() for c in self.charts],
            "layout": [dict(t) for t in self.layout],
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ParametricDashboard":
        d = d or {}
        return cls(
            name=str(d.get("name") or ""),
            connection_name=str(d.get("connection_name") or ""),
            id=str(d.get("id") or new_id()),
            charts=[ParametricChart.from_dict(c) for c in (d.get("charts") or []) if isinstance(c, dict)],
            layout=[dict(t) for t in (d.get("layout") or []) if isinstance(t, dict)],
            created_at=str(d.get("created_at") or utc_now()),
            updated_at=str(d.get("updated_at") or utc_now()),
        )
