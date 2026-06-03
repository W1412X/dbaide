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


def test_pretty_value_json():
    from dbaide.desktop.components.table import _pretty_value
    out = _pretty_value('{"a":1,"b":[2,3]}')
    assert out.startswith("{") and '"a": 1' in out and "\n" in out
    assert _pretty_value("plain text") == "plain text"
    assert _pretty_value(None) == "NULL"
    assert _pretty_value({"k": "v"}).strip().startswith("{")


def test_value_viewer_toggle_and_update(qapp):
    w = ResultTableWidget()
    w.show()
    w.load(columns=["id", "meta"],
           rows=[{"id": 1, "meta": '{"x":1}'}, {"id": 2, "meta": "hi"}], row_count=2)
    assert not w._viewer.isVisible()
    w.value_toggle.setChecked(True)
    assert w._viewer.isVisible()
    w.table.setCurrentCell(0, 1)
    qapp.processEvents()
    assert w._viewer_label.text() == "meta"
    assert '"x": 1' in w._viewer_text.toPlainText()
    w.value_toggle.setChecked(False)
    assert not w._viewer.isVisible()
    w.close()
