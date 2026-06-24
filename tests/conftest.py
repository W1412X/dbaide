"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import os

# Run any PyQt6 widget tests headlessly by default (no display needed). Must be set
# before Qt is imported anywhere, so it lives at the top of the shared conftest.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Chromium-based WebEngine aborts in headless Linux CI unless sandboxing is disabled.
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-dev-shm-usage --disable-gpu",
)

from pathlib import Path

import pytest


_SESSION_OK = {"value": False}


def pytest_sessionfinish(session, exitstatus):
    _SESSION_OK["value"] = int(exitstatus) == 0


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Qt/WebEngine can SIGABRT during interpreter teardown under offscreen Linux CI
    even when every test passed (Chromium GPU/process shutdown) — turning a green run
    red. After pytest has finished and printed its summary, exit immediately on a clean
    run to skip the C++ teardown that aborts. Failures report normally (no early exit)."""
    if _SESSION_OK["value"] and not hasattr(config, "workerinput"):   # not an xdist worker
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


@pytest.fixture(autouse=True)
def _disable_webengine_in_unit_tests(request, monkeypatch):
    """Linux CI installs PyQt6-WebEngine; real views SIGABRT under offscreen.

    Force the QTextBrowser fallback path for GUI unit tests. Tests that need a
    stand-in WebEngine widget patch ``sys.modules`` themselves; ``webengine``
    marks opt out for import-only checks.
    """
    if request.node.get_closest_marker("webengine"):
        yield
        return

    import importlib

    import dbaide.desktop.components.markdown_webview as markdown_webview

    monkeypatch.setattr(markdown_webview, "try_create_webengine_view", lambda: None)
    # Modules that did `from markdown_webview import try_create_webengine_view` hold their
    # OWN reference the patch above misses — patch each so no real WebEngine spawns (it
    # SIGABRTs at interpreter teardown under offscreen Linux CI even when tests pass).
    for modname in (
        "dbaide.desktop.components.dashboard_webview",
        "dbaide.desktop.components.answer_document",
        "dbaide.desktop.dialogs.answer_export",
        "dbaide.desktop.dialogs.chart_interaction",
    ):
        try:
            mod = importlib.import_module(modname)
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "try_create_webengine_view"):
            monkeypatch.setattr(mod, "try_create_webengine_view", lambda: None)

    # chart_block creates QWebEngineView DIRECTLY (no helper to patch). Stub the class
    # itself so any direct creation yields a harmless widget — a real Chromium view
    # SIGABRTs (exit 134) mid-run under offscreen Linux CI and kills the whole suite.
    try:
        import PyQt6.QtWebEngineWidgets as _we
        from PyQt6.QtWidgets import QWidget

        class _StubPage:
            def setBackgroundColor(self, *a, **k):
                pass

            def setWebChannel(self, *a, **k):
                pass

            def runJavaScript(self, *a, **k):
                pass

        class _StubWebEngineView(QWidget):
            def setHtml(self, *a, **k):
                pass

            def setUrl(self, *a, **k):
                pass

            def page(self):
                return _StubPage()

        monkeypatch.setattr(_we, "QWebEngineView", _StubWebEngineView)
    except Exception:  # noqa: BLE001
        pass
    yield


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
