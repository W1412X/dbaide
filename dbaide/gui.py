"""Compatibility entry point for the new DBAide desktop application."""
from __future__ import annotations

from dbaide.desktop.launcher import main


if __name__ == "__main__":
    raise SystemExit(main())

