"""Offscreen test for the memory dialog: renders items, empty state, and emits
delete/clear signals."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_memory_dialog_loads_and_clears(qapp):
    from dbaide.desktop.dialogs.memory import MemoryDialog

    dlg = MemoryDialog()
    dlg.load([
        {"id": "1", "question": "how many employees", "sql": "SELECT COUNT(*) FROM sys_user", "database": "analysis"},
    ])
    # one real row (the data list, not the empty placeholder)
    assert dlg.list.count() == 1 and dlg.list.item(0).data(0x0100) == "1"
    assert dlg._clear_btn.isEnabled()

    fired = []
    dlg.clear_requested.connect(lambda: fired.append("clear"))
    dlg._clear_btn.click()
    assert fired == ["clear"]

    dlg.load([])
    assert not dlg._clear_btn.isEnabled()  # disabled on empty


def test_right_panel_memory_wiring(qapp):
    from dbaide.desktop.views.right_panel import RightPanel

    panel = RightPanel()
    opened = []
    panel.memory_open_requested.connect(lambda: opened.append(True))
    panel.open_memory()
    assert opened == [True]            # main window is asked to load items
    panel.show_memory([{"id": "x", "question": "q", "sql": "SELECT 1", "database": ""}])
    assert panel._memory_dialog is not None and panel._memory_dialog.list.count() == 1
