"""Workflow history store for DBAide - persists workflow results for debugging and replay."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from dbaide.core.result import WorkflowResult

logger = logging.getLogger("dbaide.history")


class WorkflowHistoryStore:
    """Persists workflow results to disk for debugging and replay.

    Storage layout:
        ~/.dbaide/history/
            {connection}/
                {workflow_id}.json
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".dbaide" / "history"

    def purge_instance(self, connection_name: str) -> bool:
        """Delete all workflow history for a connection (used when it is removed)."""
        import shutil
        path = self.base_dir / connection_name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    def save(self, result: WorkflowResult) -> Path:
        """Save a workflow result to disk."""
        conn_dir = self.base_dir / result.connection_name
        conn_dir.mkdir(parents=True, exist_ok=True)

        path = conn_dir / f"{result.workflow_id}.json"
        data = result.to_dict()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.debug("saved workflow %s to %s", result.workflow_id, path)
        return path

    def load(self, connection_name: str, workflow_id: str) -> dict[str, Any] | None:
        """Load a workflow result from disk."""
        path = self.base_dir / connection_name / f"{workflow_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("failed to load workflow %s: %s", path, exc)
            return None

    def list_workflows(self, connection_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """List recent workflows for a connection."""
        conn_dir = self.base_dir / connection_name
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
        # sorting by name (the old behaviour) returned an arbitrary subset and could
        # drop genuinely recent workflows once there were more than `limit` files.
        entries.sort(key=lambda e: (e.get("created_at") or 0, e.get("completed_at") or 0), reverse=True)
        return entries[: max(0, limit)]

    def delete(self, connection_name: str, workflow_id: str) -> bool:
        """Delete a workflow result from disk."""
        path = self.base_dir / connection_name / f"{workflow_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup(self, connection_name: str, max_age_days: int = 30) -> int:
        """Delete old workflow results."""
        conn_dir = self.base_dir / connection_name
        if not conn_dir.exists():
            return 0

        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        for path in conn_dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except Exception:
                continue
        return removed
