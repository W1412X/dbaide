"""Question memory — distils information from *effective* past questions so the
agent can reuse it instead of re-exploring every time.

Each memory item is a worked example: a question that was answered successfully and
the SQL that answered it (with the database it ran in). Items are tagged with the
session they came from, so retrieval can serve both scopes:

  • session memory (会话内) — items from the current chat thread, boosted, so
    follow-ups stay coherent ("break that down by department");
  • global memory (全局) — every item for the connection, so a brand-new thread
    benefits from what earlier ones discovered.

Storage: ~/.dbaide/memory/{connection}.json  (one file per connection).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("dbaide.memory")

SCHEMA_VERSION = 1
MAX_ITEMS = 300          # prune oldest/least-used beyond this
_SQL_MAX = 600           # truncate very long SQL in an item / when rendering
_CJK = re.compile(r"[一-鿿]")


def _now() -> float:
    return time.time()


def _norm_sql(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _tokens(text: str) -> set[str]:
    """Language-agnostic token set: ascii word tokens plus individual CJK chars
    (so Chinese questions, which \\w+ doesn't split, still overlap meaningfully)."""
    low = str(text or "").lower()
    toks = set(re.findall(r"[a-z0-9_]+", low))
    toks |= set(_CJK.findall(low))
    return {t for t in toks if t}


class MemoryStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".dbaide" / "memory"

    def _path(self, connection_name: str) -> Path:
        return self.base_dir / f"{connection_name or '_default'}.json"

    # ── io ─────────────────────────────────────────────────────────────────--

    def _load(self, connection_name: str) -> dict[str, Any]:
        path = self._path(connection_name)
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "connection_name": connection_name, "items": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("items", [])
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load memory %s: %s", path, exc)
            return {"schema_version": SCHEMA_VERSION, "connection_name": connection_name, "items": []}

    def _save(self, connection_name: str, data: dict[str, Any]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path(connection_name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    # ── mutate ─────────────────────────────────────────────────────────────--

    def add(
        self,
        connection_name: str,
        *,
        question: str,
        sql: str,
        database: str = "",
        session_id: str = "",
        note: str = "",
    ) -> dict[str, Any] | None:
        """Record a worked example. No-op unless there's both a question and SQL
        (only *effective* turns are worth remembering). De-dupes on
        (question, sql): a repeat just bumps the existing item's use count."""
        question = " ".join(str(question or "").split())
        sql = _norm_sql(sql)[:_SQL_MAX]
        if not question or not sql:
            return None
        data = self._load(connection_name)
        items: list[dict[str, Any]] = data["items"]
        key = (question.lower(), sql.lower())
        for it in items:
            if (it.get("question", "").lower(), _norm_sql(it.get("sql", "")).lower()) == key:
                it["uses"] = int(it.get("uses", 0)) + 1
                it["last_used"] = _now()
                if session_id:
                    it["session_id"] = session_id
                self._save(connection_name, data)
                return it
        item = {
            "id": uuid.uuid4().hex[:12],
            "question": question,
            "sql": sql,
            "database": database,
            "session_id": session_id,
            "note": note,
            "created_at": _now(),
            "uses": 1,
            "last_used": _now(),
        }
        items.append(item)
        self._prune(items)
        self._save(connection_name, data)
        return item

    @staticmethod
    def _prune(items: list[dict[str, Any]]) -> None:
        if len(items) <= MAX_ITEMS:
            return
        # Keep the most useful/recent; drop the rest.
        items.sort(key=lambda it: (int(it.get("uses", 0)), float(it.get("last_used", 0))), reverse=True)
        del items[MAX_ITEMS:]

    def delete(self, connection_name: str, item_id: str) -> bool:
        data = self._load(connection_name)
        before = len(data["items"])
        data["items"] = [it for it in data["items"] if it.get("id") != item_id]
        if len(data["items"]) == before:
            return False
        self._save(connection_name, data)
        return True

    def clear(self, connection_name: str) -> int:
        data = self._load(connection_name)
        n = len(data["items"])
        data["items"] = []
        self._save(connection_name, data)
        return n

    # ── read / retrieve ────────────────────────────────────────────────────--

    def all(self, connection_name: str) -> list[dict[str, Any]]:
        items = list(self._load(connection_name)["items"])
        items.sort(key=lambda it: float(it.get("last_used", 0)), reverse=True)
        return items

    def relevant(
        self, connection_name: str, question: str, *, session_id: str = "", limit: int = 6
    ) -> list[dict[str, Any]]:
        """Items most relevant to ``question`` — by token overlap, with a boost for
        the current session and for frequently-reused items."""
        q = _tokens(question)
        if not q:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for it in self._load(connection_name)["items"]:
            overlap = q & _tokens(it.get("question", ""))
            if not overlap:
                continue
            score = len(overlap) / (len(q) + 1)            # fraction of the query covered
            if session_id and it.get("session_id") == session_id:
                score *= 1.6                                # session memory ranks higher
            score += min(int(it.get("uses", 0)), 5) * 0.02  # gentle popularity nudge
            scored.append((score, it))
        scored.sort(key=lambda s: (s[0], float(s[1].get("last_used", 0))), reverse=True)
        return [it for _, it in scored[: max(0, limit)]]

    @staticmethod
    def render(items: list[dict[str, Any]]) -> str:
        """A compact context block of worked examples for the agent prompt."""
        if not items:
            return ""
        lines = [
            "Known answers to similar past questions on this connection — reuse the "
            "tables/columns/filters when they fit, but verify against the live schema "
            "(don't blindly trust them):"
        ]
        for it in items:
            q = str(it.get("question") or "").strip()
            sql = _norm_sql(it.get("sql") or "")
            if len(sql) > 240:
                sql = sql[:239] + "…"
            db = str(it.get("database") or "").strip()
            suffix = f"  [db: {db}]" if db else ""
            line = f'- "{q}" → {sql}{suffix}'
            note = str(it.get("note") or "").strip()
            if note:
                line += f"  ({note})"
            lines.append(line)
        return "\n".join(lines)
