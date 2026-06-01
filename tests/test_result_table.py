"""Result table: unified alignment + full value access for truncated cells."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from dbaide.desktop.components.table import ResultTableWidget, _full_text


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_alignment_is_unified_by_type(qapp):
    w = ResultTableWidget()
    w.load(columns=["id", "note"], rows=[{"id": 1, "note": "x"}, {"id": 2, "note": "y"}], row_count=2)
    id_item, note_item = w.table.item(0, 0), w.table.item(0, 1)
    # numbers right, text left, both vertically centred
    assert int(id_item.textAlignment()) & int(Qt.AlignmentFlag.AlignRight)
    assert int(note_item.textAlignment()) & int(Qt.AlignmentFlag.AlignLeft)
    assert int(id_item.textAlignment()) & int(Qt.AlignmentFlag.AlignVCenter)
    assert int(note_item.textAlignment()) & int(Qt.AlignmentFlag.AlignVCenter)
    # the numeric header lines up with its column
    assert int(w.table.horizontalHeaderItem(0).textAlignment()) & int(Qt.AlignmentFlag.AlignRight)


def test_long_cell_truncates_display_but_keeps_full_value(qapp):
    long = "L" * 400
    w = ResultTableWidget()
    w.load(columns=["note"], rows=[{"note": long}], row_count=1)
    item = w.table.item(0, 0)
    assert item.text().endswith("…") and len(item.text()) < len(long)
    assert item.toolTip() == long          # full value on hover
    assert _full_text(w._rows[0]["note"]) == long  # and available to the detail dialog
