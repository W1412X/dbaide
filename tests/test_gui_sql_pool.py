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


def test_indicator_visibility(qapp):
    from dbaide.desktop.views.sql_pool import SqlPoolIndicator
    governor.configure(0)
    try:
        ind = SqlPoolIndicator()
        ind.refresh()
        assert ind.isHidden() is True                  # off + idle → no clutter
        tok = governor.acquire("SELECT 1", 0)          # off but something running → monitor
        ind.refresh()
        assert ind.isHidden() is False
        governor.release(tok)
        governor.configure(1000)
        ind.refresh()
        assert ind.isHidden() is False                 # armed → always visible (discoverable)
        from dbaide.i18n import t as _t
        assert ind.text() == _t("sqlpool.title")       # armed + idle → tidy chip, not "0·0·0%"
        tok2 = governor.acquire("SELECT 2", 100)
        ind.refresh()
        assert "%" in ind.text()                        # active → live counts + budget %
        governor.release(tok2)
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


def test_dialog_monitors_running_sql_when_governor_off(qapp):
    from dbaide.desktop.views.sql_pool import SqlPoolDialog
    governor.configure(0)
    try:
        dlg = SqlPoolDialog()
        dlg.refresh()
        assert dlg._running.rowCount() == 0
        assert dlg._bar.isHidden() is True             # no budget bar when off
        assert dlg._queued_col.isHidden() is True      # whole queue column collapses when off
        assert dlg._empty.isHidden() is False
        tok = governor.acquire("SELECT 1 FROM t", 0, connection="c")  # running, ungoverned
        dlg.refresh()
        assert dlg._running.rowCount() == 1            # monitor shows it even with governor off
        assert dlg._running.item(0, 1).text() == "—"   # cost unknown (not estimated when off)
        assert dlg._empty.isHidden() is True
        governor.release(tok)
    finally:
        governor.configure(0)
