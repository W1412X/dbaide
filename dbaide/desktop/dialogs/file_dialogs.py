"""Theme-aware wrappers around ``QFileDialog``.

These wrappers force the non-native Qt dialog so the app stylesheet, popup
styling, and window chrome apply consistently across platforms.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QWidget

from dbaide.desktop.theme import app_style
from dbaide.desktop.window_chrome import apply_window_background, install_top_level_chrome


class ThemedFileDialog(QFileDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chrome_installed = False
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.setStyleSheet(app_style())
        apply_window_background(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._chrome_installed:
            self._chrome_installed = True
            apply_window_background(self)
            install_top_level_chrome(self, layout=self.layout())


def _prepare_dialog(
    parent: QWidget | None,
    caption: str,
    directory: str,
    file_filter: str,
) -> ThemedFileDialog:
    dialog = ThemedFileDialog(parent)
    dialog.setWindowTitle(str(caption or ""))
    dialog.setModal(True)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    if file_filter:
        dialog.setNameFilter(file_filter)
        dialog.selectNameFilter(file_filter)
    target = str(directory or "").strip()
    if target:
        path = Path(target).expanduser()
        if path.name and path.suffix:
            dialog.setDirectory(str(path.parent))
            dialog.selectFile(path.name)
        else:
            dialog.setDirectory(str(path))
            dialog.selectFile(str(path))
    return dialog


def get_open_file_name(
    parent: QWidget | None,
    caption: str,
    directory: str = "",
    file_filter: str = "",
) -> tuple[str, str]:
    dialog = _prepare_dialog(parent, caption, directory, file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return "", dialog.selectedNameFilter()
    files = dialog.selectedFiles()
    return (files[0] if files else "", dialog.selectedNameFilter())


def get_save_file_name(
    parent: QWidget | None,
    caption: str,
    directory: str = "",
    file_filter: str = "",
) -> tuple[str, str]:
    dialog = _prepare_dialog(parent, caption, directory, file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return "", dialog.selectedNameFilter()
    files = dialog.selectedFiles()
    return (files[0] if files else "", dialog.selectedNameFilter())
