"""Relative date/time helpers for parameterized dashboards.

A parameter's default (or a value sent from a control) may be a *dynamic token*
like ``@today`` or ``@days_ago:30`` instead of a hardcoded value, so a board
always opens on "today / this month / this year". Tokens resolve to concrete
values at run time, deterministically given the reference date (injected for
tests). Unknown tokens resolve to ``None`` and the caller keeps the original.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _today(today: date | None) -> date:
    return today or date.today()


def _months_ago(d: date, n: int) -> date:
    month_index = (d.year * 12 + (d.month - 1)) - n
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def resolve_dynamic(token: Any, today: date | None = None) -> Any:
    """Return the concrete value for an ``@token``; ``None`` if not a known token."""
    if not isinstance(token, str) or not token.startswith("@"):
        return None
    t = _today(today)
    key, _, arg = token[1:].partition(":")
    key = key.strip().lower()
    try:
        if key == "today":
            return t.isoformat()
        if key == "yesterday":
            return (t - timedelta(days=1)).isoformat()
        if key == "month_start":
            return t.replace(day=1).isoformat()
        if key == "year_start":
            return t.replace(month=1, day=1).isoformat()
        if key == "quarter_start":
            return t.replace(month=((t.month - 1) // 3) * 3 + 1, day=1).isoformat()
        if key == "days_ago":
            return (t - timedelta(days=int(arg or 0))).isoformat()
        if key == "months_ago":
            return _months_ago(t, int(arg or 0)).isoformat()
        if key == "year":
            return t.year
        if key == "month":
            return t.month
        if key == "month_str":
            return t.strftime("%Y-%m")
    except (ValueError, OverflowError):
        return None
    return None


def resolve_value(value: Any, today: date | None = None) -> Any:
    """Resolve a value that may be a dynamic token; pass through everything else."""
    if isinstance(value, str) and value.startswith("@"):
        out = resolve_dynamic(value, today)
        return value if out is None else out
    return value
