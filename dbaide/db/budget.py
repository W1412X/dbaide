"""QueryBudget: per-instance semaphore that caps concurrent in-flight queries.

Every SQL statement issued by the asset builder, the agent loop, or the GUI goes
through one shared :class:`QueryBudget` per database instance. Because a query
holds its slot for the full duration of its connection, the same semaphore bounds
*both* concurrent queries and concurrent physical connections — no connection pool
library required.

The budget also accumulates lightweight stats (total queries, peak in-flight,
per-caller counts) so the cost of a build or an agent turn is observable.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(slots=True)
class BudgetStats:
    total_queries: int = 0
    peak_inflight: int = 0
    by_caller: dict[str, int] = field(default_factory=dict)


class QueryBudget:
    """A bounded semaphore + stats, shared by all callers of one instance."""

    def __init__(self, instance: str, *, max_inflight: int) -> None:
        self.instance = instance
        self._max_inflight = max(1, int(max_inflight))
        self._sem = threading.BoundedSemaphore(self._max_inflight)
        self._lock = threading.Lock()
        self._inflight = 0
        self.stats = BudgetStats()

    @property
    def max_inflight(self) -> int:
        return self._max_inflight

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    @contextmanager
    def acquire(self, caller: str = "agent") -> Iterator[None]:
        self._sem.acquire()
        with self._lock:
            self._inflight += 1
            self.stats.total_queries += 1
            self.stats.by_caller[caller] = self.stats.by_caller.get(caller, 0) + 1
            if self._inflight > self.stats.peak_inflight:
                self.stats.peak_inflight = self._inflight
        try:
            yield
        finally:
            with self._lock:
                self._inflight -= 1
            self._sem.release()

    def reset_stats(self) -> None:
        with self._lock:
            self.stats = BudgetStats()


# ── Per-instance registry ────────────────────────────────────────────────────

_registry_lock = threading.Lock()
_registry: dict[str, QueryBudget] = {}


def for_instance(instance: str, *, max_inflight: int) -> QueryBudget:
    """Return the shared budget for ``instance``, creating it on first use.

    If a budget already exists with a different ``max_inflight`` (the user changed
    the policy), it is rebuilt so the new limit takes effect.
    """
    key = instance or "<default>"
    with _registry_lock:
        existing = _registry.get(key)
        if existing is None or existing.max_inflight != max(1, int(max_inflight)):
            existing = QueryBudget(key, max_inflight=max_inflight)
            _registry[key] = existing
        return existing


def reset_registry() -> None:
    with _registry_lock:
        _registry.clear()
