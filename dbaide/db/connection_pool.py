"""Per-instance physical connection pools for remote database adapters.

QueryBudget remains the source of truth for concurrency. The pool only makes each
budget slot cheaper by reusing an already-authenticated connection.

Pooled connections are not kept forever: each has a max lifetime and an idle timeout.
A background reaper closes connections that have sat idle too long or outlived their
lifetime, so dbaide does not hold physical connections against a production database
indefinitely (and a connection the server has since dropped is retired before reuse).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Hashable


ConnectionFactory = Callable[[], object]
ConnectionValidator = Callable[[object], bool]

# Retire an idle connection after this long unused, and any connection after this long
# alive, regardless of use. Bounds long-lived connections against a production DB and
# limits exposure to server-side idle drops. ``None`` on a pool disables either bound.
DEFAULT_IDLE_TIMEOUT = 300.0     # 5 min
DEFAULT_MAX_LIFETIME = 1800.0    # 30 min
_REAP_INTERVAL = 30.0            # background sweep cadence


@dataclass(frozen=True, slots=True)
class PoolKey:
    instance: str
    kind: str
    database: str
    session_timezone: str = ""


class PooledConnection:
    def __init__(self, pool: "ConnectionPool", conn: object, epoch: int = 0) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_released", False)
        # The pool's epoch when this connection was checked out. close_all() bumps the
        # epoch, so a connection acquired before a pool reset is recognised as stale on
        # release and discarded instead of being pooled against a reset _total count.
        object.__setattr__(self, "_epoch", epoch)

    def __enter__(self) -> "PooledConnection":
        return self

    def __exit__(self, exc_type, _exc, _tb) -> bool:
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    def close(self) -> None:
        if self._released:
            return
        try:
            self._conn.rollback()
        except Exception:
            pass
        object.__setattr__(self, "_released", True)
        self._pool.release(self._conn, self._epoch)


class ConnectionPool:
    def __init__(
        self,
        *,
        key: PoolKey,
        max_size: int,
        factory: ConnectionFactory,
        validator: ConnectionValidator | None = None,
        idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT,
        max_lifetime: float | None = DEFAULT_MAX_LIFETIME,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.key = key
        self.max_size = max(1, int(max_size))
        self._factory = factory
        self._validator = validator or (lambda _conn: True)
        self._idle_timeout = idle_timeout
        self._max_lifetime = max_lifetime
        self._clock = clock
        self._cond = threading.Condition()
        self._idle: list[tuple[object, float]] = []   # (connection, idle-since timestamp)
        self._born: dict[int, float] = {}             # id(conn) -> creation timestamp
        self._total = 0
        self._epoch = 0

    def acquire(self) -> PooledConnection:
        with self._cond:
            while True:
                while self._idle:
                    conn, idle_since = self._idle.pop()
                    if not self._expired(conn, idle_since) and self._valid(conn):
                        return PooledConnection(self, conn, self._epoch)
                    # Idle too long, past its lifetime, or failed validation → retire it
                    # (frees the slot so acquire can build a fresh one below).
                    self._total -= 1
                    self._forget(conn)
                    self._close_physical(conn)
                if self._total < self.max_size:
                    self._total += 1
                    break
                self._cond.wait()
            epoch = self._epoch
        try:
            conn = self._factory()
        except Exception:
            with self._cond:
                # Only reclaim the slot if no reset happened while we were building —
                # close_all() already zeroed _total for the old epoch.
                if epoch == self._epoch:
                    self._total -= 1
                self._cond.notify()
            raise
        with self._cond:
            self._born[id(conn)] = self._clock()
        return PooledConnection(self, conn, epoch)

    def release(self, conn: object, epoch: int | None = None) -> None:
        with self._cond:
            # A connection acquired before a close_all() belongs to a dead epoch: its
            # slot is no longer counted in _total, so don't pool it (that would leave
            # _idle out of sync with _total and let acquire() exceed max_size) and
            # don't decrement _total (it was already reset). Just close it.
            if epoch is not None and epoch != self._epoch:
                self._forget(conn)
                self._close_physical(conn)
                self._cond.notify()
                return
            if not self._over_lifetime(conn) and self._valid(conn):
                self._idle.append((conn, self._clock()))
            else:
                self._total -= 1
                self._forget(conn)
                self._close_physical(conn)
            self._cond.notify()

    def reap(self) -> None:
        """Close idle connections that have timed out or outlived their max lifetime.

        Called by the background reaper so idle connections are released even when no
        query ever comes to trigger the lazy check in acquire(). Only touches idle
        connections; in-use ones are checked on release."""
        doomed: list[object] = []
        with self._cond:
            keep: list[tuple[object, float]] = []
            for conn, idle_since in self._idle:
                if self._expired(conn, idle_since):
                    self._total -= 1
                    self._forget(conn)
                    doomed.append(conn)
                else:
                    keep.append((conn, idle_since))
            self._idle = keep
            if doomed:
                self._cond.notify_all()
        for conn in doomed:
            self._close_physical(conn)

    def close_all(self) -> None:
        with self._cond:
            self._epoch += 1
            idle, self._idle = self._idle, []
            self._born.clear()
            self._total = 0
            self._cond.notify_all()
        for conn, _idle_since in idle:
            self._close_physical(conn)

    def _expired(self, conn: object, idle_since: float) -> bool:
        """Idle longer than idle_timeout, or alive longer than max_lifetime."""
        now = self._clock()
        if self._idle_timeout is not None and now - idle_since > self._idle_timeout:
            return True
        return self._over_lifetime(conn, now)

    def _over_lifetime(self, conn: object, now: float | None = None) -> bool:
        if self._max_lifetime is None:
            return False
        born = self._born.get(id(conn))
        if born is None:
            return False
        return (self._clock() if now is None else now) - born > self._max_lifetime

    def _forget(self, conn: object) -> None:
        self._born.pop(id(conn), None)

    def _valid(self, conn: object) -> bool:
        """Check whether *conn* is still usable.  Returns False on validator
        exception — callers are responsible for closing invalid connections
        (avoids double-close when both ``_valid`` and the caller close)."""
        try:
            return bool(self._validator(conn))
        except Exception:
            return False

    @staticmethod
    def _close_physical(conn: object) -> None:
        try:
            conn.close()
        except Exception:
            pass


_registry_lock = threading.Lock()
_registry: dict[Hashable, ConnectionPool] = {}
_reaper_started = False
_reaper_stop = threading.Event()


def _reap_all_pools() -> None:
    with _registry_lock:
        pools = list(_registry.values())
    for pool in pools:
        try:
            pool.reap()
        except Exception:
            pass


def _ensure_reaper_locked() -> None:
    """Start the background reaper once. Assumes _registry_lock is held."""
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def _loop() -> None:
        while not _reaper_stop.wait(_REAP_INTERVAL):
            _reap_all_pools()

    threading.Thread(target=_loop, name="dbaide-conn-reaper", daemon=True).start()


def for_key(
    key: PoolKey,
    *,
    max_size: int,
    factory: ConnectionFactory,
    validator: ConnectionValidator | None = None,
    idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT,
    max_lifetime: float | None = DEFAULT_MAX_LIFETIME,
) -> ConnectionPool:
    registry_key = (key.instance or "<default>", key.kind, key.database, key.session_timezone)
    with _registry_lock:
        _ensure_reaper_locked()
        pool = _registry.get(registry_key)
        if pool is None or pool.max_size != max(1, int(max_size)):
            if pool is not None:
                pool.close_all()
            pool = ConnectionPool(
                key=key, max_size=max_size, factory=factory, validator=validator,
                idle_timeout=idle_timeout, max_lifetime=max_lifetime,
            )
            _registry[registry_key] = pool
        return pool


def reset_registry() -> None:
    with _registry_lock:
        pools = list(_registry.values())
        _registry.clear()
    for pool in pools:
        pool.close_all()
