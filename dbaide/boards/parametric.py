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

PARAM_TYPES = ("text", "number", "date", "enum")
COMBINE_MODES = ("single", "union", "join")


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in known}


@dataclass
class ParamSpec:
    """A control on the dashboard: a typed, optionally-constrained parameter."""

    name: str                       # placeholder used in SQL as :name
    type: str = "text"              # text | number | date | enum
    label: str = ""
    default: Any = None
    options: list[Any] = field(default_factory=list)   # allowed values for type=enum

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
