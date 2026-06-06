"""Per-instance physical connection pools for remote database adapters.

QueryBudget remains the source of truth for concurrency. The pool only makes each
budget slot cheaper by reusing an already-authenticated connection.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Hashable


ConnectionFactory = Callable[[], object]
ConnectionValidator = Callable[[object], bool]


@dataclass(frozen=True, slots=True)
class PoolKey:
    instance: str
    kind: str
    database: str


class PooledConnection:
    def __init__(self, pool: "ConnectionPool", conn: object) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_released", False)

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
        self._pool.release(self._conn)


class ConnectionPool:
    def __init__(
        self,
        *,
        key: PoolKey,
        max_size: int,
        factory: ConnectionFactory,
        validator: ConnectionValidator | None = None,
    ) -> None:
        self.key = key
        self.max_size = max(1, int(max_size))
        self._factory = factory
        self._validator = validator or (lambda _conn: True)
        self._cond = threading.Condition()
        self._idle: list[object] = []
        self._total = 0

    def acquire(self) -> PooledConnection:
        with self._cond:
            while True:
                while self._idle:
                    conn = self._idle.pop()
                    if self._valid(conn):
                        return PooledConnection(self, conn)
                    self._total -= 1
                    self._close_physical(conn)
                if self._total < self.max_size:
                    self._total += 1
                    break
                self._cond.wait()
        try:
            conn = self._factory()
        except Exception:
            with self._cond:
                self._total -= 1
                self._cond.notify()
            raise
        return PooledConnection(self, conn)

    def release(self, conn: object) -> None:
        with self._cond:
            if self._valid(conn):
                self._idle.append(conn)
            else:
                self._total -= 1
                self._close_physical(conn)
            self._cond.notify()

    def close_all(self) -> None:
        with self._cond:
            idle, self._idle = self._idle, []
            self._total = 0
            self._cond.notify_all()
        for conn in idle:
            self._close_physical(conn)

    def _valid(self, conn: object) -> bool:
        try:
            return bool(self._validator(conn))
        except Exception:
            self._close_physical(conn)
            return False

    @staticmethod
    def _close_physical(conn: object) -> None:
        try:
            conn.close()
        except Exception:
            pass


_registry_lock = threading.Lock()
_registry: dict[Hashable, ConnectionPool] = {}


def for_key(
    key: PoolKey,
    *,
    max_size: int,
    factory: ConnectionFactory,
    validator: ConnectionValidator | None = None,
) -> ConnectionPool:
    registry_key = (key.instance or "<default>", key.kind, key.database)
    with _registry_lock:
        pool = _registry.get(registry_key)
        if pool is None or pool.max_size != max(1, int(max_size)):
            if pool is not None:
                pool.close_all()
            pool = ConnectionPool(key=key, max_size=max_size, factory=factory, validator=validator)
            _registry[registry_key] = pool
        return pool


def reset_registry() -> None:
    with _registry_lock:
        pools = list(_registry.values())
        _registry.clear()
    for pool in pools:
        pool.close_all()
