"""Qt WebEngine must be initialized before QApplication."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.webengine
def test_ensure_webengine_before_qapplication_allows_import():
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        pytest.skip("QApplication already exists in this pytest session")

    from dbaide.desktop.platform_ui import ensure_webengine_before_qapplication

    webengine_ready = ensure_webengine_before_qapplication()
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        assert webengine_ready is True
        assert QWebEngineView is not None
    except ImportError:
        assert webengine_ready is False
