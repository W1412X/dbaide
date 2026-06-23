from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_text_input_dialog_returns_trimmed_value(qapp, monkeypatch):
    from dbaide.desktop.dialogs import text_input as mod
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(mod.TextInputDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(mod.TextInputDialog, "value", lambda self: "  renamed  ".strip())
    value, ok = mod.get_text(None, "Rename", "Title:", text="Old")
    assert ok is True
    assert value == "renamed"


def test_choice_dialog_returns_selected_key(qapp):
    from dbaide.desktop.dialogs.message_dialog import ChoiceDialog

    dialog = ChoiceDialog(
        None,
        "Export scope",
        "Pick one",
        choices=[("page", "Current page"), ("all", "All rows")],
    )
    dialog._accept_choice("all")
    assert dialog.choice() == "all"


def _assert_action_below_content(dialog, content, action) -> None:
    from PyQt6.QtCore import QPoint

    dialog.show()
    QApplication.processEvents()
    content_bottom = content.mapTo(dialog, content.rect().bottomLeft()).y()
    action_top = action.mapTo(dialog, QPoint(0, 0)).y()
    assert action_top >= content_bottom


def test_backup_dialog_fields_keep_readable_height(qapp):
    from dbaide.desktop.components.inputs import COMPACT_DIALOG_FIELD_HEIGHT
    from dbaide.desktop.dialogs.backup import BackupDialog

    dialog = BackupDialog(None, "conn", "platform", table="sys_user", scope="table")
    dialog.show()
    qapp.processEvents()
    assert dialog._fmt_combo.height() >= COMPACT_DIALOG_FIELD_HEIGHT
    assert dialog._batch_spin.height() >= COMPACT_DIALOG_FIELD_HEIGHT
    _assert_action_below_content(dialog, dialog._batch_spin, dialog._start_btn)


def test_text_input_dialog_action_row_below_field(qapp):
    from PyQt6.QtWidgets import QPushButton

    from dbaide.desktop.dialogs.text_input import TextInputDialog

    dialog = TextInputDialog(None, "Rename", "Title:", text="Old")
    ok_btn = next(btn for btn in dialog.findChildren(QPushButton) if btn.text())
    _assert_action_below_content(dialog, dialog._input, ok_btn)


def test_answer_export_dialog_padding_spins_keep_readable_height(qapp):
    from dbaide.desktop.components.inputs import STANDARD_FIELD_HEIGHT
    from dbaide.desktop.dialogs.answer_export import AnswerExportDialog

    dialog = AnswerExportDialog(
        None,
        answer="hello",
        charts=[],
        title="Demo",
        theme={"bg": "#fff", "text": "#111"},
    )
    for spin in dialog._spins.values():
        assert spin.height() >= STANDARD_FIELD_HEIGHT


def test_build_assets_dialog_resource_spins_keep_readable_height(qapp):
    from dbaide.desktop.components.inputs import STANDARD_FIELD_HEIGHT
    from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog

    dialog = BuildAssetsDialog(
        connection_name="local",
        databases=[{"name": "demo", "has_assets": False}],
    )
    dialog.show()
    qapp.processEvents()
    assert dialog._workers.height() >= STANDARD_FIELD_HEIGHT
    assert dialog._timeout.height() >= STANDARD_FIELD_HEIGHT
    _assert_action_below_content(dialog, dialog._timeout, dialog._build_btn)


def test_themed_file_dialog_forces_non_native_mode(qapp):
    from dbaide.desktop.dialogs.file_dialogs import ThemedFileDialog

    dialog = ThemedFileDialog()
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)


def test_prepare_dialog_prefills_extensionless_save_name(qapp, tmp_path):
    """An extension-less save filename must pre-fill the name box, not be treated as a
    directory (regression: the old `path.suffix` heuristic dropped it)."""
    from dbaide.desktop.dialogs.file_dialogs import _prepare_dialog

    d = _prepare_dialog(None, "Save", str(tmp_path / "report"), "HTML (*.html)")
    sel = d.selectedFiles()
    assert sel and sel[0].endswith("report")           # name pre-filled
    d.deleteLater()

    d2 = _prepare_dialog(None, "Save", str(tmp_path / "report.html"), "HTML (*.html)")
    sel2 = d2.selectedFiles()
    assert sel2 and sel2[0].endswith("report.html")    # name with extension still works
    d2.deleteLater()

    # An existing directory is used as the starting folder (not selected as a file).
    d3 = _prepare_dialog(None, "Open", str(tmp_path), "")
    import os
    assert os.path.realpath(d3.directory().absolutePath()) == os.path.realpath(str(tmp_path))
    d3.deleteLater()


def _find_conn_row(dialog, name):
    from PyQt6.QtCore import Qt
    for i in range(dialog.conn_list.count()):
        if dialog.conn_list.item(i).data(Qt.ItemDataRole.UserRole) == name:
            return i
    return -1


def test_excel_collection_panel_toggles_and_manages_workbooks(qapp, tmp_path, monkeypatch):
    """Selecting an Excel-collection connection shows the workbook manager (not the host
    form); adding/removing workbooks updates it."""
    from dbaide.desktop.dialogs.settings import SettingsDialog
    from dbaide.desktop.dialogs import settings as settings_mod
    from dbaide.ingest import ExcelCollection, collection_dir

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    # build a real collection on disk
    sales = tmp_path / "sales.csv"; sales.write_text("amt\n10\n20\n", encoding="utf-8")
    cust = tmp_path / "customers.csv"; cust.write_text("name\nAda\n", encoding="utf-8")
    col = ExcelCollection(collection_dir(cfg_dir, "shop"))
    col.add([sales])

    connections = [
        {"name": "shop", "type": "sqlite", "path": str(col.db_path)},
        {"name": "prod", "type": "mysql", "host": "localhost"},
    ]
    dialog = SettingsDialog(
        connections=connections, models=[], config_dir=str(cfg_dir), initial_page="connections",
    )

    # selecting the collection shows the panel, hides the DB form
    dialog.conn_list.setCurrentRow(_find_conn_row(dialog, "shop"))
    qapp.processEvents()
    assert not dialog.workbook_panel.isHidden()
    assert dialog._conn_form_area.isHidden()

    # selecting a normal connection flips back to the form
    dialog.conn_list.setCurrentRow(_find_conn_row(dialog, "prod"))
    qapp.processEvents()
    assert not dialog._conn_form_area.isHidden()
    assert dialog.workbook_panel.isHidden()

    # add a workbook through the panel's flow (stub the file picker)
    dialog.conn_list.setCurrentRow(_find_conn_row(dialog, "shop"))
    qapp.processEvents()
    monkeypatch.setattr(dialog, "_pick_spreadsheets", lambda: [str(cust)])
    changed = []
    dialog.excel_collection_changed.connect(changed.append)
    dialog._excel_add_workbook()
    assert {w.source_filename for w in col.workbooks()} == {"sales.csv", "customers.csv"}
    assert changed == ["shop"]

    # remove a (non-last) workbook with confirmation stubbed to True
    monkeypatch.setattr(settings_mod, "dialog_confirm", lambda *a, **k: True)
    wid = next(w.id for w in col.workbooks() if w.source_filename == "sales.csv")
    dialog._excel_remove_workbook(wid)
    assert [w.source_filename for w in col.workbooks()] == ["customers.csv"]

    dialog.deleteLater()
