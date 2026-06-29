"""Process-wide cost-budget admission governor for SQL execution.

Every executed query is admitted through one shared budget. Two rules, both keyed
to the configured ``max_inflight_cost`` (the cost *threshold*):

1. **Per query** — a single query may not cost more than the threshold. Such a
   query could never fit the budget, so it is rejected up front (``CostExceeded``)
   rather than queued forever.
2. **In aggregate** — the sum of the costs of all *currently executing* queries may
   not exceed the threshold. A query that doesn't fit yet waits in a **FIFO** queue;
   the head of the queue is admitted as soon as enough budget frees up.

Cost unit: EXPLAIN-estimated scanned rows (best-effort — an unknown estimate counts
as 0, so it never blocks). A budget of ``0`` disables the governor entirely: no
gating, no queue, no tracking, and ``acquire()`` returns ``None`` so callers run
straight through.

The governor is a process-wide singleton (``governor``) shared by every execution
path — agent answers, dashboards, the SQL editor, table browsing, MCP, CLI — so the
in-flight total is global, not per session or per connection. It is thread-safe: SQL
runs on background threads and many may contend at once.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


class CostExceeded(ValueError):
    """A single query's estimated cost exceeds the whole budget — it can never fit."""


class Cancelled(Exception):
    """A queued query was cancelled while waiting for budget."""


def _now() -> float:
    return time.monotonic()


def _truncate(sql: str, *, limit: int = 160) -> str:
    one_line = " ".join(str(sql or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


@dataclass
class _Entry:
    seq: int
    label: str
    cost: int
    connection: str
    enqueued_at: float
    started_at: float = 0.0


class CostGovernor:
    """FIFO, cost-bounded admission control for read-only SQL."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._budget = 0
        self._seq = 0
        self._queue: list[_Entry] = []        # FIFO waiters (index 0 == head)
        self._running: dict[int, _Entry] = {}  # token → entry

    # -- configuration --------------------------------------------------------

    def configure(self, budget: int | None) -> None:
        """Set the cost threshold (``max_inflight_cost``). 0/None disables gating.

        Raising the budget can let waiters in, so we wake everyone to re-check."""
        with self._cond:
            try:
                self._budget = max(0, int(budget or 0))
            except (TypeError, ValueError):
                self._budget = 0
            self._cond.notify_all()

    @property
    def budget(self) -> int:
        return self._budget

    @property
    def enabled(self) -> bool:
        return self._budget > 0

    # -- admission ------------------------------------------------------------

    def acquire(self, label: str, cost: int, *, connection: str = "",
                cancel=None) -> int | None:
        """Block until admitted; return an opaque token to pass to :meth:`release`.

        Returns ``None`` when the governor is disabled (the caller then runs
        ungoverned). Raises :class:`CostExceeded` if ``cost`` exceeds the whole
        budget. ``cancel`` is an optional ``() -> bool`` predicate polled while
        waiting; when it turns true the entry leaves the queue and
        :class:`Cancelled` is raised.
        """
        with self._cond:
            if self._budget <= 0:
                return None
            cost = max(0, int(cost or 0))
            if cost > self._budget:
                raise CostExceeded(
                    f"Query estimated ~{cost:,} rows, over the in-flight cost budget of "
                    f"{self._budget:,}. Add filters or narrow the range, then retry."
                )
            self._seq += 1
            entry = _Entry(seq=self._seq, label=_truncate(label), cost=cost,
                           connection=connection, enqueued_at=_now())
            self._queue.append(entry)
            self._cond.notify_all()
            try:
                while True:
                    if cancel is not None and cancel():
                        raise Cancelled()
                    # Strict FIFO: only the head of the queue is eligible, so a cheap
                    # query can't jump ahead of an expensive one that arrived first.
                    # A re-lowered budget can strand an already-queued entry whose cost
                    # now exceeds it; admit it anyway when it runs alone (in-flight == 0)
                    # so the queue can never deadlock.
                    if self._queue and self._queue[0] is entry:
                        in_flight = self._in_flight_cost()
                        if in_flight + cost <= self._budget or in_flight == 0:
                            self._queue.pop(0)
                            entry.started_at = _now()
                            self._running[entry.seq] = entry
                            self._cond.notify_all()
                            return entry.seq
                    self._cond.wait(timeout=0.5)
            except BaseException:
                # cancelled or interrupted before admission → drop from the queue
                if entry in self._queue:
                    self._queue.remove(entry)
                self._cond.notify_all()
                raise

    def release(self, token: int | None) -> None:
        """Free the budget held by an admitted query. No-op for ``None`` tokens."""
        if token is None:
            return
        with self._cond:
            self._running.pop(token, None)
            self._cond.notify_all()

    def _in_flight_cost(self) -> int:
        return sum(e.cost for e in self._running.values())

    # -- introspection (for the pool viewer) ----------------------------------

    def snapshot(self) -> dict:
        """A point-in-time view of the pool for the UI (no locks held by the caller)."""
        with self._cond:
            now = _now()
            running = sorted(self._running.values(), key=lambda e: e.started_at)
            return {
                "enabled": self._budget > 0,
                "budget": self._budget,
                "in_flight_cost": self._in_flight_cost(),
                "running_count": len(self._running),
                "queued_count": len(self._queue),
                "running": [{
                    "label": e.label, "cost": e.cost, "connection": e.connection,
                    "elapsed_s": max(0.0, now - e.started_at),
                } for e in running],
                "queued": [{
                    "label": e.label, "cost": e.cost, "connection": e.connection,
                    "waited_s": max(0.0, now - e.enqueued_at),
                } for e in self._queue],
            }


# Process-wide singleton — import and use directly.
governor = CostGovernor()
