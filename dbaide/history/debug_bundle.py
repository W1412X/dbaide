"""Debug bundle for DBAide - packages workflow data for issue reporting."""
from __future__ import annotations

import json
import logging
import platform
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from dbaide.core.result import WorkflowResult

logger = logging.getLogger("dbaide.debug_bundle")


class DebugBundle:
    """Packages workflow data into a ZIP bundle for issue reporting.

    Bundle contents:
        manifest.json      - metadata (version, platform, timestamps)
        result.json         - full WorkflowResult
        trace.json          - trace events
        environment.json    - Python version, platform, packages
        config.json         - sanitized config (no secrets)
    """

    def __init__(self, result: WorkflowResult) -> None:
        self.result = result
        self.created_at = time.time()

    def to_bytes(self) -> bytes:
        """Create ZIP bundle in memory."""
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(self._manifest(), indent=2))
            zf.writestr("result.json", json.dumps(self.result.to_dict(), indent=2, default=str))
            zf.writestr("trace.json", json.dumps([e.to_dict() for e in self.result.trace], indent=2))
            zf.writestr("environment.json", json.dumps(self._environment(), indent=2))
        return buf.getvalue()

    def save(self, path: Path) -> Path:
        """Save ZIP bundle to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        logger.debug("saved debug bundle to %s", path)
        return path

    def _manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "created_at": self.created_at,
            "workflow_id": self.result.workflow_id,
            "question": self.result.question,
            "status": self.result.status.value,
            "connection_name": self.result.connection_name,
            "error_count": len(self.result.errors),
            "warning_count": len(self.result.warnings),
            "trace_count": len(self.result.trace),
        }

    def _environment(self) -> dict[str, Any]:
        try:
            import dbaide
            version = getattr(dbaide, "__version__", "unknown")
        except Exception:
            version = "unknown"

        return {
            "dbaide_version": version,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        }


def create_debug_bundle(result: WorkflowResult, output_dir: Path | None = None) -> Path:
    """Create and save a debug bundle."""
    bundle = DebugBundle(result)
    if output_dir is None:
        output_dir = Path.home() / ".dbaide" / "debug"
    filename = f"dbaide-debug-{result.workflow_id}-{int(time.time())}.zip"
    return bundle.save(output_dir / filename)
