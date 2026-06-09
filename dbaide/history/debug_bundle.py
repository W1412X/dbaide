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
        result.json         - full WorkflowResult (when available)
        trace.json          - trace events
        environment.json    - Python version, platform, packages
        config.json         - sanitized config (no secrets)
        context.json        - optional desktop/session context
        app.log.tail.txt    - last lines of the app log (when available)
    """

    def __init__(
        self,
        result: WorkflowResult | None = None,
        *,
        config: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        log_tail: list[str] | None = None,
    ) -> None:
        self.result = result
        self.config = config or {}
        self.context = context or {}
        self.log_tail = list(log_tail or [])
        self.created_at = time.time()

    def to_bytes(self) -> bytes:
        """Create ZIP bundle in memory."""
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(self._manifest(), indent=2))
            if self.result is not None:
                zf.writestr("result.json", json.dumps(self.result.to_dict(), indent=2, default=str))
                zf.writestr(
                    "trace.json",
                    json.dumps([e.to_dict() for e in self.result.trace], indent=2, default=str),
                )
            elif self.context.get("trace"):
                zf.writestr("trace.json", json.dumps(self.context.get("trace"), indent=2, default=str))
            zf.writestr("environment.json", json.dumps(self._environment(), indent=2))
            if self.config:
                zf.writestr("config.json", json.dumps(self.config, indent=2, default=str))
            if self.context:
                zf.writestr("context.json", json.dumps(self.context, indent=2, default=str))
            if self.log_tail:
                zf.writestr("app.log.tail.txt", "\n".join(self.log_tail) + "\n")
        return buf.getvalue()

    def save(self, path: Path) -> Path:
        """Save ZIP bundle to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        logger.debug("saved debug bundle to %s", path)
        return path

    def _manifest(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "schema_version": 2,
            "created_at": self.created_at,
            "bundle_kind": "workflow" if self.result is not None else "desktop",
        }
        if self.result is None:
            base.update({
                "connection_name": self.context.get("connection_name", ""),
                "active_session": self.context.get("session_id", ""),
                "trace_count": len(self.context.get("trace") or []),
            })
            return base
        return {
            **base,
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
    """Create and save a debug bundle from a workflow result (CLI path)."""
    bundle = DebugBundle(result)
    if output_dir is None:
        output_dir = Path.home() / ".dbaide" / "debug"
    filename = f"dbaide-debug-{result.workflow_id}-{int(time.time())}.zip"
    return bundle.save(output_dir / filename)


def create_desktop_debug_bundle(
    *,
    config: dict[str, Any],
    context: dict[str, Any] | None = None,
    log_tail: list[str] | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Create a support bundle from the running desktop app."""
    bundle = DebugBundle(None, config=config, context=context or {}, log_tail=log_tail)
    if output_dir is None:
        output_dir = Path.home() / ".dbaide" / "debug"
    conn = str((context or {}).get("connection_name") or "desktop")
    # Sanitise: connection names are user-chosen and may contain path
    # separators ('a/b') or traversal ('../x'), which would place the zip
    # outside the intended output directory.
    safe_conn = "".join(c if c.isalnum() or c in "-_." else "_" for c in conn) or "desktop"
    filename = f"dbaide-debug-{safe_conn}-{int(time.time())}.zip"
    return bundle.save(output_dir / filename)
