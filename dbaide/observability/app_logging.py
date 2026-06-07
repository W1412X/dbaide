"""Application logging — stderr + rotating file under ~/.dbaide/logs/."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".dbaide" / "logs"
DEFAULT_LOG_FILE = "dbaide.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def log_directory() -> Path:
    raw = os.environ.get("DBAIDE_LOG_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_LOG_DIR


def setup_app_logging(
    *,
    verbose: int = 0,
    quiet: bool = False,
    log_file: str = DEFAULT_LOG_FILE,
) -> Path:
    """Configure root logging to stderr and a rotating file. Idempotent."""
    if quiet:
        level = logging.ERROR
    elif verbose >= 2:
        level = logging.DEBUG
    elif verbose >= 1:
        level = logging.INFO
    else:
        level = logging.INFO

    env_level = os.environ.get("DBAIDE_LOG_LEVEL", "").strip().upper()
    if env_level in logging.getLevelNamesMapping():
        level = logging.getLevelNamesMapping()[env_level]

    log_dir = log_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    if getattr(root, "_dbaide_logging_configured", False):
        root.setLevel(level)
        return log_path

    root.setLevel(level)
    root.handlers.clear()

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(level)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger("dbaide.logging").warning("file logging disabled: %s", exc)
        log_path = Path()

    root._dbaide_logging_configured = True  # type: ignore[attr-defined]
    logging.getLogger("dbaide").debug("logging to %s", log_path or "(stderr only)")
    return log_path


def tail_log_lines(*, max_lines: int = 200) -> list[str]:
    """Return the last N lines of the app log (best-effort)."""
    path = log_directory() / DEFAULT_LOG_FILE
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, max_lines) :]
