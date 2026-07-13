"""Custom dark desktop application for DBAide."""

from typing import Any

__all__ = ["DesktopService"]


def __getattr__(name: str) -> Any:
    """Keep the Qt client from importing the in-daemon service implementation."""
    if name == "DesktopService":
        from dbaide.desktop.service import DesktopService
        return DesktopService
    raise AttributeError(name)
