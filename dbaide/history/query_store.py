"""Per-connection SQL query history (append-only JSONL, capped).

Every statement run from the Workbench SQL editor is recorded here so it can be
recalled later — DBeaver's "SQL editor history". Storage is a small JSONL file
per connection under ``~/.dbaide/query_history``; we keep the most recent
``MAX_ENTRIES`` and collapse a run that is identical to the immediately previous
one (re-running the same query just bumps its timestamp).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES = 500


class QueryHistoryStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".dbaide" / "query_history"

    def _path(self, connection_name: str) -> Path:
        return self.base_dir / f"{connection_name or '_default'}.jsonl"

    def record(
        self,
        connection_name: str,
        sql: str,
        *,
        ok: bool = True,
        row_count: int | None = None,
        elapsed_ms: float | None = None,
        database: str = "",
    ) -> None:
        sql = (sql or "").strip()
        if not sql:
            return
        entries = self._read(connection_name)
        # Collapse a re-run of the most recent query into a timestamp bump.
        if entries and entries[-1].get("sql") == sql:
            entries.pop()
        entries.append({
            "sql": sql,
            "ok": bool(ok),
            "row_count": row_count,
            "elapsed_ms": round(float(elapsed_ms), 1) if elapsed_ms is not None else None,
            "database": database or "",
            "ts": time.time(),
        })
        entries = entries[-MAX_ENTRIES:]
        self._write(connection_name, entries)

    def recent(self, connection_name: str, limit: int = 200) -> list[dict[str, Any]]:
        """Most-recent-first history entries."""
        entries = self._read(connection_name)
        entries.reverse()
        return entries[: max(0, limit)]

    def clear(self, connection_name: str) -> None:
        path = self._path(connection_name)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning("failed to clear query history %s: %s", path, exc)

    # ── io ──────────────────────────────────────────────────────────────────--

    def _read(self, connection_name: str) -> list[dict[str, Any]]:
        path = self._path(connection_name)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:  # noqa: BLE001
            logger.warning("failed to read query history %s: %s", path, exc)
        return out

    def _write(self, connection_name: str, entries: list[dict[str, Any]]) -> None:
        path = self._path(connection_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in entries)
            path.write_text(body + ("\n" if body else ""), encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            logger.warning("failed to write query history %s: %s", path, exc)
