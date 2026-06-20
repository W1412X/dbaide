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
