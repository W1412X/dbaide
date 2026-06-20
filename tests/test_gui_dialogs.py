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


def test_themed_file_dialog_forces_non_native_mode(qapp):
    from dbaide.desktop.dialogs.file_dialogs import ThemedFileDialog

    dialog = ThemedFileDialog()
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
