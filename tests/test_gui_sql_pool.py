"""GUI test for the SQL pool viewer (status-bar indicator + dialog)."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from dbaide.core.sql_governor import governor


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_indicator_hidden_when_disabled_visible_when_enabled(qapp):
    from dbaide.desktop.views.sql_pool import SqlPoolIndicator
    governor.configure(0)
    try:
        ind = SqlPoolIndicator()
        ind.refresh()
        assert ind.isHidden() is True                 # off → no status-bar clutter
        governor.configure(1000)
        ind.refresh()
        assert ind.isHidden() is False                 # armed → indicator shows
        assert "0" in ind.text()                       # 0 running / 0 queued
    finally:
        governor.configure(0)


def test_dialog_lists_running_and_queued(qapp):
    from dbaide.desktop.views.sql_pool import SqlPoolDialog
    governor.configure(100)
    started = threading.Event()
    try:
        running = governor.acquire("SELECT * FROM orders", 60, connection="shop")  # 60/100

        def worker():
            started.set()
            tok = governor.acquire("SELECT * FROM big", 60, connection="shop")      # queues
            governor.release(tok)

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        assert _wait_for(lambda: governor.snapshot()["queued_count"] == 1)

        dlg = SqlPoolDialog()
        dlg.refresh()
        assert dlg._running.rowCount() == 1
        assert dlg._queued.rowCount() == 1
        assert dlg._running.item(0, 0).text() == "SELECT * FROM orders"
        assert dlg._running.item(0, 1).text() == "60"          # est. cost
        assert dlg._running.item(0, 2).text() == "shop"        # connection
        assert "60" in dlg._budget_lbl.text() and "100" in dlg._budget_lbl.text()
        assert dlg._empty.isHidden() is True

        governor.release(running)                              # let the queued one in
        assert _wait_for(lambda: governor.snapshot()["queued_count"] == 0)
        th.join(timeout=2)
    finally:
        governor.configure(0)


def test_dialog_shows_disabled_state(qapp):
    from dbaide.desktop.views.sql_pool import SqlPoolDialog
    governor.configure(0)
    dlg = SqlPoolDialog()
    dlg.refresh()
    assert dlg._running.rowCount() == 0 and dlg._queued.rowCount() == 0
    assert dlg._empty.isHidden() is False
