"""Persistent stores for saved questions and dashboards.

Single global JSON file each under ``~/.dbaide/boards/`` (saved questions and
dashboards can both span connections), atomic write, upsert by id. Mirrors the
shape of :class:`dbaide.annotations.store.AnnotationStore`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from dbaide.boards.models import Dashboard, SavedQuestion, Tile, utc_now

logger = logging.getLogger("dbaide.boards")

DEFAULT_BOARDS_DIR = Path.home() / ".dbaide" / "boards"


class _JsonStore:
    """A single JSON file holding a list of records under one key, atomic save."""

    filename = ""
    payload_key = ""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).expanduser()
        else:
            self.base_dir = Path(os.environ.get("DBAIDE_BOARDS", DEFAULT_BOARDS_DIR)).expanduser()
        # Refreshes write snapshots from a background thread while the UI thread may
        # pin/delete — serialise read-modify-write so updates aren't lost. Reentrant
        # so a compound op (add_tile → update) can hold the lock across both steps.
        self._lock = threading.RLock()

    def _path(self) -> Path:
        return self.base_dir / self.filename

    def _load_raw(self) -> list[dict[str, Any]]:
        path = self._path()
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("boards_read_failed: %s", exc)
            return []
        items = data.get(self.payload_key) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [dict(x) for x in items if isinstance(x, dict)]

    def _save_raw(self, records: list[dict[str, Any]]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": utc_now(), self.payload_key: records}
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
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


class SavedQuestionStore(_JsonStore):
    """CRUD for pinned, re-runnable questions."""

    filename = "questions.json"
    payload_key = "questions"

    def list(self) -> list[SavedQuestion]:
        return [SavedQuestion.from_dict(r) for r in self._load_raw()]

    def get(self, question_id: str) -> SavedQuestion | None:
        for r in self._load_raw():
            if str(r.get("id") or "") == str(question_id):
                return SavedQuestion.from_dict(r)
        return None

    def upsert(self, question: SavedQuestion) -> SavedQuestion:
        with self._lock:
            records = self._load_raw()
            question.updated_at = utc_now()
            out = question.to_dict()
            for i, r in enumerate(records):
                if str(r.get("id") or "") == str(question.id):
                    out["created_at"] = r.get("created_at") or out["created_at"]
                    records[i] = out
                    self._save_raw(records)
                    return question
            records.append(out)
            self._save_raw(records)
            return question

    def save_snapshot(
        self,
        question_id: str,
        *,
        chart_spec: dict[str, Any] | None,
        columns: list[str],
        row_count: int,
    ) -> SavedQuestion | None:
        """Persist a fresh result after a tile refresh (keeps the rest intact)."""
        with self._lock:
            records = self._load_raw()
            for i, r in enumerate(records):
                if str(r.get("id") or "") == str(question_id):
                    r["chart_spec"] = chart_spec
                    r["columns"] = list(columns or [])
                    r["row_count"] = int(row_count or 0)
                    r["last_run_at"] = utc_now()
                    r["updated_at"] = r["last_run_at"]
                    records[i] = r
                    self._save_raw(records)
                    return SavedQuestion.from_dict(r)
            return None

    def rename(self, question_id: str, name: str) -> SavedQuestion | None:
        with self._lock:
            records = self._load_raw()
            for i, r in enumerate(records):
                if str(r.get("id") or "") == str(question_id):
                    r["name"] = str(name or "").strip() or r.get("name") or "未命名"
                    r["updated_at"] = utc_now()
                    records[i] = r
                    self._save_raw(records)
                    return SavedQuestion.from_dict(r)
            return None

    def delete(self, question_id: str) -> bool:
        with self._lock:
            records = self._load_raw()
            kept = [r for r in records if str(r.get("id") or "") != str(question_id)]
            if len(kept) == len(records):
                return False
            self._save_raw(kept)
            return True


class DashboardStore(_JsonStore):
    """CRUD for dashboards; tiles reference saved questions by id."""

    filename = "dashboards.json"
    payload_key = "dashboards"

    def list(self) -> list[Dashboard]:
        return [Dashboard.from_dict(r) for r in self._load_raw()]

    def get(self, dashboard_id: str) -> Dashboard | None:
        for r in self._load_raw():
            if str(r.get("id") or "") == str(dashboard_id):
                return Dashboard.from_dict(r)
        return None

    def create(self, name: str) -> Dashboard:
        board = Dashboard(name=str(name or "").strip() or "Dashboard")
        with self._lock:
            records = self._load_raw()
            records.append(board.to_dict())
            self._save_raw(records)
        return board

    def update(self, board: Dashboard) -> Dashboard:
        with self._lock:
            records = self._load_raw()
            board.updated_at = utc_now()
            out = board.to_dict()
            for i, r in enumerate(records):
                if str(r.get("id") or "") == str(board.id):
                    out["created_at"] = r.get("created_at") or out["created_at"]
                    records[i] = out
                    self._save_raw(records)
                    return board
            records.append(out)
            self._save_raw(records)
            return board

    def delete(self, dashboard_id: str) -> bool:
        with self._lock:
            records = self._load_raw()
            kept = [r for r in records if str(r.get("id") or "") != str(dashboard_id)]
            if len(kept) == len(records):
                return False
            self._save_raw(kept)
            return True

    def add_tile(self, dashboard_id: str, question_id: str, *, w: int = 6, h: int = 5) -> Dashboard | None:
        with self._lock:   # held across get → update so the tile add is atomic
            board = self.get(dashboard_id)
            if board is None:
                return None
            qid = str(question_id)
            if any(t.question_id == qid for t in board.tiles):
                return board   # already on this board — don't create a duplicate tile
            x, y = board.next_slot(w=w, h=h)
            board.tiles.append(Tile(question_id=qid, x=x, y=y, w=w, h=h))
            return self.update(board)

    def remove_tile(self, dashboard_id: str, question_id: str) -> Dashboard | None:
        """Remove one tile from one board (the saved question itself is untouched)."""
        with self._lock:
            board = self.get(dashboard_id)
            if board is None:
                return None
            qid = str(question_id)
            board.tiles = [t for t in board.tiles if t.question_id != qid]
            return self.update(board)

    def detach_question(self, question_id: str) -> int:
        """Drop every tile referencing a (now-deleted) question. Returns tiles removed."""
        with self._lock:
            records = self._load_raw()
            removed = 0
            changed = False
            for r in records:
                tiles = r.get("tiles") or []
                kept = [t for t in tiles if str((t or {}).get("question_id") or "") != str(question_id)]
                if len(kept) != len(tiles):
                    removed += len(tiles) - len(kept)
                    r["tiles"] = kept
                    r["updated_at"] = utc_now()
                    changed = True
            if changed:
                self._save_raw(records)
            return removed
