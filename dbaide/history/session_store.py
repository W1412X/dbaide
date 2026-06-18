"""Chat session store — groups conversation turns into named, persistent sessions.

A *session* (会话) is a chat thread; it holds an ordered list of *turns* (对话),
each turn being one question → answer with its SQL and agent trace. This is the
higher-level grouping the desktop UI navigates; the per-workflow
``WorkflowHistoryStore`` still keeps the raw workflow JSON for debug bundles.

Storage layout:
    ~/.dbaide/sessions/
        {connection}/
            {session_id}.json
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("dbaide.sessions")

SCHEMA_VERSION = 1
DEFAULT_TITLE = "New chat"
_TITLE_MAX = 60


def _safe_name(name: str) -> str:
    """Filesystem-safe path component. Connection names (and ids) are user-chosen and
    could contain path separators or traversal ('../x'); collapse anything that isn't a
    plain identifier char so sessions always stay inside base_dir (mirrors
    history.query_store / history.store / observability.query_log)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "_default"))


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _title_from_question(question: str) -> str:
    q = " ".join(str(question or "").split()).strip()
    if not q:
        return DEFAULT_TITLE
    return q if len(q) <= _TITLE_MAX else q[: _TITLE_MAX - 1] + "…"


def make_turn(
    *,
    question: str,
    answer_markdown: str = "",
    selected_sql: str = "",
    status: str = "completed",
    workflow_id: str = "",
    trace: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    clarifications: list[str] | None = None,
    disclosed_tables: list[str] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    schema_scope: dict[str, Any] | None = None,
    created_at: float | None = None,
    charts: list[dict[str, Any]] | None = None,
    executed_sqls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a turn dict from a workflow result's salient fields.

    `clarifications` are the user-confirmed criteria from this turn (口径). Future
    turns in the same session inherit them as binding context, so we have to
    persist them — they're what makes "by Beijing time" stick across turns.
    `disclosed_tables` records which tables this turn touched, so the next turn
    can skip rediscovery when continuing the same line of questioning.
    `attachments` are the UI-level composer chips (db/table the user pinned for
    this prompt); `schema_scope` is the structured form sent to the agent.
    Persisting both lets the UI restore the attachment tags *and* lets the agent
    carry forward pinned scope on follow-up turns.
    """
    return {
        "workflow_id": workflow_id,
        "question": question,
        "answer_markdown": answer_markdown,
        "selected_sql": selected_sql,
        "status": status,
        "trace": trace or [],
        "meta": meta or {},
        "clarifications": list(clarifications or []),
        "disclosed_tables": list(disclosed_tables or []),
        "attachments": list(attachments or []),
        "schema_scope": schema_scope or {},
        "created_at": created_at if created_at is not None else _now(),
        "charts": list(charts or []),
        "executed_sqls": [dict(item) for item in (executed_sqls or []) if isinstance(item, dict)],
    }


class ChatSessionStore:
    """Persists chat sessions (a session = an ordered list of conversation turns)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".dbaide" / "sessions"
        self._lock = threading.Lock()

    # ── paths ────────────────────────────────────────────────────────────────

    def _conn_dir(self, connection_name: str) -> Path:
        return self.base_dir / _safe_name(connection_name)

    def _path(self, connection_name: str, session_id: str) -> Path:
        return self._conn_dir(connection_name) / f"{_safe_name(session_id)}.json"

    def purge_instance(self, connection_name: str) -> bool:
        """Delete all chat sessions for a connection (used when it is removed)."""
        import shutil
        path = self._conn_dir(connection_name)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    # ── read ───────────────────────────────────────────────────────────────--

    def load(self, connection_name: str, session_id: str) -> dict[str, Any] | None:
        path = self._path(connection_name, session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load session %s: %s", path, exc)
            return None

    def list_sessions(self, connection_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Session summaries (no turn bodies), most-recently-updated first."""
        conn_dir = self._conn_dir(connection_name)
        if not conn_dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in conn_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            turns = data.get("turns") or []
            last_q = turns[-1].get("question") if turns else ""
            out.append({
                "session_id": data.get("session_id", path.stem),
                "title": data.get("title") or DEFAULT_TITLE,
                "connection_name": data.get("connection_name", connection_name),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", data.get("created_at", 0)),
                "turn_count": len(turns),
                "last_question": last_q or "",
            })
        out.sort(key=lambda e: (e.get("updated_at") or 0, e.get("created_at") or 0), reverse=True)
        return out[: max(0, limit)]

    # ── write ──────────────────────────────────────────────────────────────--

    def _write(self, session: dict[str, Any]) -> Path:
        conn_dir = self._conn_dir(session["connection_name"])
        conn_dir.mkdir(parents=True, exist_ok=True)
        path = conn_dir / f"{session['session_id']}.json"
        content = json.dumps(session, ensure_ascii=False, indent=2, default=str)
        # Atomic write: temp file + rename so a crash mid-write cannot corrupt
        # the session file (path.write_text is not atomic on most filesystems).
        fd, tmp = tempfile.mkstemp(dir=str(conn_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    def create(self, connection_name: str, title: str = "") -> dict[str, Any]:
        now = _now()
        session = {
            "schema_version": SCHEMA_VERSION,
            "session_id": _new_id(),
            "title": (title or DEFAULT_TITLE).strip() or DEFAULT_TITLE,
            "connection_name": connection_name,
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }
        self._write(session)
        return session

    def append_turn(
        self, connection_name: str, session_id: str, turn: dict[str, Any],
        *, messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """Append a turn; auto-titles an untitled session from the first question.

        When ``messages`` is provided, the completed turn and the session's LLM
        message stream are written in a SINGLE locked read-modify-write. Doing
        both under one lock (rather than append_turn + a separate save_messages)
        closes a lost-update window: the message stream is the agent's entire
        cross-turn memory, so two interleaved writes could silently drop it.
        """
        with self._lock:
            session = self.load(connection_name, session_id)
            if session is None:
                return None
            session.setdefault("turns", []).append(turn)
            if messages is not None:
                session["messages"] = messages
            session["updated_at"] = _now()
            if not session.get("title") or session["title"] == DEFAULT_TITLE:
                session["title"] = _title_from_question(str(turn.get("question") or ""))
            self._write(session)
            return session

    def rename(self, connection_name: str, session_id: str, title: str) -> bool:
        with self._lock:
            session = self.load(connection_name, session_id)
            if session is None:
                return False
            session["title"] = (title or "").strip() or DEFAULT_TITLE
            session["updated_at"] = _now()
            self._write(session)
            return True

    def load_messages(self, connection_name: str, session_id: str) -> list[dict[str, str]] | None:
        """Load the persisted message stream for a session. Returns None if absent."""
        session = self.load(connection_name, session_id)
        if not isinstance(session, dict):
            return None
        messages = session.get("messages")
        return messages if isinstance(messages, list) else None

    def save_messages(self, connection_name: str, session_id: str,
                      messages: list[dict[str, str]]) -> None:
        """Persist the message stream (called after each turn completes)."""
        with self._lock:
            session = self.load(connection_name, session_id)
            if session is None:
                return
            session["messages"] = messages
            session["updated_at"] = _now()
            self._write(session)

    def delete(self, connection_name: str, session_id: str) -> bool:
        path = self._path(connection_name, session_id)
        if path.exists():
            path.unlink()
            return True
        return False
