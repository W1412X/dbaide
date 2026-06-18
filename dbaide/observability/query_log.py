"""QueryLog: records every SQL statement DBAide runs, so it can be inspected.

Each instance gets one :class:`QueryLog`. Every query (build, agent, or GUI) is
recorded with its caller, database, SQL text, elapsed time, row count and status.
Records are:

  * appended to ``~/.dbaide/logs/queries/{instance}.jsonl`` (durable audit trail);
  * kept in a bounded in-memory ring buffer (fast access for UI / CLI tail);
  * pushed to any subscribers (the GUI subscribes to show live detail).

This is the single source of truth for "what SQL did the tool just run?".
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("dbaide.querylog")

RING_CAPACITY = 2000
_SQL_PREVIEW_MAX = 4000


def _default_log_dir() -> Path:
    override = os.environ.get("DBAIDE_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".dbaide" / "logs" / "queries"


@dataclass(slots=True)
class QueryLogEntry:
    ts: float
    instance: str
    caller: str          # build | agent | gui | join_validate | ...
    database: str
    sql: str
    elapsed_ms: float
    row_count: int
    status: str          # ok | error
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_name(instance: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (instance or "default"))


class QueryLog:
    def __init__(self, instance: str, *, log_dir: Path | None = None, persist: bool = True) -> None:
        self.instance = instance or "default"
        self._persist = persist
        self._dir = log_dir or _default_log_dir()
        self._path = self._dir / f"{_safe_name(self.instance)}.jsonl"
        self._lock = threading.Lock()
        self._ring: deque[QueryLogEntry] = deque(maxlen=RING_CAPACITY)
        self._subscribers: list[Callable[[QueryLogEntry], None]] = []

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        caller: str,
        database: str,
        sql: str,
        elapsed_ms: float,
        row_count: int = 0,
        status: str = "ok",
        error: str = "",
    ) -> QueryLogEntry:
        entry = QueryLogEntry(
            ts=time.time(),
            instance=self.instance,
            caller=caller or "agent",
            database=database or "",
            sql=(sql or "")[:_SQL_PREVIEW_MAX],
            elapsed_ms=round(float(elapsed_ms or 0.0), 2),
            row_count=int(row_count or 0),
            status=status or "ok",
            error=(error or "")[:500],
        )
        with self._lock:
            self._ring.append(entry)
            subscribers = list(self._subscribers)
            # Append under the lock: the per-instance logger is shared across the
            # concurrent multi-run slots, so writing the JSONL line outside the lock
            # could interleave two entries on the same file. Subscribers run OUTSIDE
            # the lock (they may be slow / re-enter).
            if self._persist:
                self._append_jsonl(entry)
        for cb in subscribers:
            try:
                cb(entry)
            except Exception:  # subscribers must never break query execution
                logger.debug("query-log subscriber raised", exc_info=True)
        return entry

    def _append_jsonl(self, entry: QueryLogEntry) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("query-log write failed: %s", exc)

    def recent(self, limit: int = 100) -> list[QueryLogEntry]:
        with self._lock:
            items = list(self._ring)
        return items[-limit:] if limit > 0 else items

    def summary(self) -> dict:
        with self._lock:
            items = list(self._ring)
        total = len(items)
        elapsed = sum(e.elapsed_ms for e in items)
        errors = sum(1 for e in items if e.status != "ok")
        by_caller: dict[str, int] = {}
        for e in items:
            by_caller[e.caller] = by_caller.get(e.caller, 0) + 1
        return {
            "total": total,
            "elapsed_ms": round(elapsed, 2),
            "errors": errors,
            "by_caller": by_caller,
        }

    def subscribe(self, cb: Callable[[QueryLogEntry], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(cb)

        def _unsubscribe() -> None:
            with self._lock:
                if cb in self._subscribers:
                    self._subscribers.remove(cb)

        return _unsubscribe

    def tail_file(self, limit: int = 50) -> list[dict]:
        """Read the last ``limit`` entries from the persisted jsonl file."""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# ── Per-instance registry ────────────────────────────────────────────────────

_registry_lock = threading.Lock()
_registry: dict[str, QueryLog] = {}


def for_instance(instance: str, *, log_dir: Path | None = None, persist: bool = True) -> QueryLog:
    key = instance or "default"
    with _registry_lock:
        existing = _registry.get(key)
        if existing is None:
            existing = QueryLog(key, log_dir=log_dir, persist=persist)
            _registry[key] = existing
        return existing


def purge_instance(instance: str, *, log_dir: Path | None = None) -> bool:
    """Delete the persisted query-audit log for a connection (used on removal)."""
    key = instance or "default"
    with _registry_lock:
        _registry.pop(key, None)  # drop the in-memory ring too
    path = (log_dir or _default_log_dir()) / f"{_safe_name(key)}.jsonl"
    if path.exists():
        path.unlink()
        return True
    return False


def reset_registry() -> None:
    with _registry_lock:
        _registry.clear()
