"""Workflow history store for DBAide - persists workflow results for debugging and replay."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from dbaide.core.result import WorkflowResult

logger = logging.getLogger("dbaide.history")


def _safe_name(name: str) -> str:
    """Filesystem-safe path component. Connection names are user-chosen and could
    contain path separators or traversal ('../x'); collapse anything that isn't a
    plain identifier char so history always stays inside base_dir (mirrors
    history.query_store / observability.query_log)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "_default"))


class WorkflowHistoryStore:
    """Persists workflow results to disk for debugging and replay.

    Storage layout:
        ~/.dbaide/history/
            {connection}/
                {workflow_id}.json
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".dbaide" / "history"

    def _conn_dir(self, connection_name: str) -> Path:
        return self.base_dir / _safe_name(connection_name)

    def _workflow_path(self, connection_name: str, workflow_id: str) -> Path:
        return self._conn_dir(connection_name) / f"{_safe_name(workflow_id)}.json"

    def purge_instance(self, connection_name: str) -> bool:
        """Delete all workflow history for a connection (used when it is removed)."""
        import shutil
        path = self._conn_dir(connection_name)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    def save(self, result: WorkflowResult) -> Path:
        """Save a workflow result to disk."""
        conn_dir = self._conn_dir(result.connection_name)
        conn_dir.mkdir(parents=True, exist_ok=True)

        path = conn_dir / f"{_safe_name(result.workflow_id)}.json"
        data = result.to_dict()
        content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        # Atomic write: temp file + rename so a crash mid-write cannot truncate
        # the workflow result file.
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
        logger.debug("saved workflow %s to %s", result.workflow_id, path)
        return path

    def load(self, connection_name: str, workflow_id: str) -> dict[str, Any] | None:
        """Load a workflow result from disk."""
        path = self._workflow_path(connection_name, workflow_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("failed to load workflow %s: %s", path, exc)
            return None

    def list_workflows(self, connection_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """List recent workflows for a connection."""
        conn_dir = self._conn_dir(connection_name)
        if not conn_dir.exists():
            return []

        entries = []
        for path in conn_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append({
                    "workflow_id": data.get("workflow_id", path.stem),
                    "question": data.get("question", ""),
                    "status": data.get("status", ""),
                    "created_at": data.get("created_at", 0),
                    "completed_at": data.get("completed_at", 0),
                    "file_path": str(path),
                })
            except Exception:
                continue

        # "Recent" must mean most-recent by time. Filenames are random uuids, so
        # name sorting would return an arbitrary subset and could drop genuinely
        # recent workflows once there were more than `limit` files.
        entries.sort(key=lambda e: (e.get("created_at") or 0, e.get("completed_at") or 0), reverse=True)
        return entries[: max(0, limit)]

    def delete(self, connection_name: str, workflow_id: str) -> bool:
        """Delete a workflow result from disk."""
        path = self._workflow_path(connection_name, workflow_id)
        if path.exists():
            path.unlink()
            return True
        return False
