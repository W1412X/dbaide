"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import os

# Run any PyQt6 widget tests headlessly by default (no display needed). Must be set
# before Qt is imported anywhere, so it lives at the top of the shared conftest.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


@pytest.fixture(autouse=True)
def drain_qt_pool():
    """Wait for any background ServiceWorker to finish and deliver its queued signals
    before the test ends, so a late callback can't fire at a half-destroyed widget and
    crash Qt during interpreter shutdown. No-op when there's no QApplication."""
    yield
    try:
        from PyQt6.QtCore import QCoreApplication, QThreadPool
    except Exception:
        return
    app = QCoreApplication.instance()
    if app is not None:
        QThreadPool.globalInstance().waitForDone(3000)
        app.processEvents()


@pytest.fixture(autouse=True)
def isolated_query_log(tmp_path: Path) -> None:
    """Redirect query-log writes to a temp dir and reset resource registries."""
    from dbaide.db import budget as budget_mod
    from dbaide.db import policy as policy_mod
    from dbaide.observability import query_log

    root = tmp_path / "dbaide_logs"
    previous = os.environ.get("DBAIDE_LOG_DIR")
    os.environ["DBAIDE_LOG_DIR"] = str(root)
    budget_mod.reset_registry()
    policy_mod.clear_cache()
    query_log.reset_registry()
    yield
    budget_mod.reset_registry()
    policy_mod.clear_cache()
    query_log.reset_registry()
    if previous is None:
        os.environ.pop("DBAIDE_LOG_DIR", None)
    else:
        os.environ["DBAIDE_LOG_DIR"] = previous
