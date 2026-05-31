"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import os

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_join_catalog(tmp_path: Path) -> None:
    """Keep join catalog writes out of ~/.dbaide during tests."""
    root = tmp_path / "dbaide_joins"
    previous = os.environ.get("DBAIDE_JOINS")
    os.environ["DBAIDE_JOINS"] = str(root)
    yield
    if previous is None:
        os.environ.pop("DBAIDE_JOINS", None)
    else:
        os.environ["DBAIDE_JOINS"] = previous
