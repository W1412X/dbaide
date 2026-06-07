"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import os

# Run any PyQt6 widget tests headlessly by default (no display needed). Must be set
# before Qt is imported anywhere, so it lives at the top of the shared conftest.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_local_state(tmp_path: Path) -> None:
    """Keep asset, join and annotation stores out of ~/.dbaide during tests."""
    keys = {
        "DBAIDE_ASSETS": tmp_path / "dbaide_assets",
        "DBAIDE_JOINS": tmp_path / "dbaide_joins",
        "DBAIDE_ANNOTATIONS": tmp_path / "dbaide_annotations",
    }
    previous = {key: os.environ.get(key) for key in keys}
    for key, path in keys.items():
        os.environ[key] = str(path)
    yield
    for key, old_value in previous.items():
        if old_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_value


@pytest.fixture(autouse=True)
def drain_qt_pool():
    """Wait for any background ServiceWorker to finish and deliver its queued signals
    before the test ends, so a late callback can't fire at a half-destroyed widget and
    crash Qt during interpreter shutdown. No-op when there's no QApplication."""
    yield
    try:
        from PyQt6.QtCore import QCoreApplication, QThreadPool
        from PyQt6 import sip
    except Exception:
        return
    app = QCoreApplication.instance()
    if app is not None and not sip.isdeleted(app):
        QThreadPool.globalInstance().waitForDone(3000)


@pytest.fixture(autouse=True)
def isolated_query_log(tmp_path: Path) -> None:
    """Redirect query-log writes to a temp dir and reset resource registries."""
    from dbaide.db import budget as budget_mod
    from dbaide.db import connection_pool as pool_mod
    from dbaide.db import policy as policy_mod
    from dbaide.observability import query_log

    root = tmp_path / "dbaide_logs"
    previous = os.environ.get("DBAIDE_LOG_DIR")
    os.environ["DBAIDE_LOG_DIR"] = str(root)
    budget_mod.reset_registry()
    pool_mod.reset_registry()
    policy_mod.clear_cache()
    query_log.reset_registry()
    yield
    budget_mod.reset_registry()
    pool_mod.reset_registry()
    policy_mod.clear_cache()
    query_log.reset_registry()
    if previous is None:
        os.environ.pop("DBAIDE_LOG_DIR", None)
    else:
        os.environ["DBAIDE_LOG_DIR"] = previous
