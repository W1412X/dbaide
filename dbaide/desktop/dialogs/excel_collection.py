"""Staging dialog for creating a new Excel/CSV collection.

The user names the connection, adds one or more files, and can rename each resulting
table before anything is built. Returns ``(connection_name, [ImportSpec, …])`` on accept.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import ElidingLabel, compact_button, ghost_action_button
from dbaide.desktop.components.inputs import (
    STANDARD_FIELD_HEIGHT,
    configure_compact_field,
    dialog_action_row,
)
from dbaide.desktop.dialogs.file_dialogs import get_open_file_names
from dbaide.desktop.dialogs.message_dialog import warn as dialog_warn
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.ingest import SUPPORTED_EXTS, ImportSpec, is_valid_collection_name
from dbaide.i18n import t as _pt


class _StagedRow(QFrame):
    def __init__(self, path: Path, on_remove) -> None:
        super().__init__()
        self.path = path
        self.header_anchors: dict[str, tuple[int, int]] = {}   # sheet → (header_row, start_col)
        self.sheets: list[str] | None = None                   # selected sheets (None = all)
        self.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:8px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(8)
        self.name_edit = QLineEdit(path.stem)
        self.name_edit.setPlaceholderText(_pt("excel.table_name_ph"))
        configure_compact_field(self.name_edit, height=STANDARD_FIELD_HEIGHT)
        self.name_edit.setMaximumWidth(180)
        source = ElidingLabel(path.name)
        source.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent; border:none;")
        source.setToolTip(str(path))
        lay.addWidget(self.name_edit)
        lay.addWidget(source, 1)
        self._header_btn = ghost_action_button(_pt("excel.header_btn"))
        self._header_btn.clicked.connect(self._pick_header)
        lay.addWidget(self._header_btn)
        remove = ghost_action_button("✕")
        remove.setToolTip(_pt("excel.remove_workbook"))
        remove.clicked.connect(lambda: on_remove(self))
        lay.addWidget(remove)

    def _pick_header(self) -> None:
        from dbaide.desktop.dialogs.header_preview import pick_header_rows
        try:
            chosen = pick_header_rows(self.window(), self.path, self.header_anchors or None)
        except Exception as exc:  # noqa: BLE001
            dialog_warn(self.window(), _pt("excel.header_title"),
                        _pt("excel.err.import_failed", error=str(exc)))
            return
        if chosen is not None:
            anchors, included = chosen
            self.header_anchors = anchors
            self.sheets = included          # restrict import to the picked sheets
            self._header_btn.setText(_pt("excel.header_set"))   # mark the row as customised

    def name(self) -> str:
        return self.name_edit.text().strip()


class NewCollectionDialog(ChromeDialog):
    def __init__(self, parent, existing_names: set[str], *, mode: str = "create") -> None:
        super().__init__(parent)
        self._mode = mode                       # "create" (with name field) | "add" (files only)
        self._existing = {n.lower() for n in existing_names}
        self._rows: list[_StagedRow] = []
        title = _pt("excel.new_title") if mode == "create" else _pt("excel.add_title")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(app_style())
        self.setAcceptDrops(True)                  # drop spreadsheet files straight onto the dialog

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        heading = QLabel(title)
        heading.setStyleSheet(f"color:{Theme.TEXT}; font-size:15px; font-weight:700; background:transparent;")
        root.addWidget(heading)
        hint = QLabel(_pt("excel.new_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        root.addWidget(hint)

        self._name = QLineEdit()
        if mode == "create":
            name_label = QLabel(_pt("excel.conn_name"))
            name_label.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; font-weight:500; background:transparent;")
            root.addWidget(name_label)
            self._name.setPlaceholderText(_pt("excel.conn_name_ph"))
            configure_compact_field(self._name, height=STANDARD_FIELD_HEIGHT)
            root.addWidget(self._name)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setMinimumHeight(150)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._empty = QLabel(_pt("excel.no_files"))
        self._empty.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        self._rows_layout.addWidget(self._empty)
        self._rows_layout.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        add_files = compact_button(_pt("excel.add_files"), width=124)
        add_files.clicked.connect(self._add_files)
        add_row = QHBoxLayout()
        add_row.addWidget(add_files)
        add_row.addStretch(1)
        root.addLayout(add_row)

        actions_host, actions = dialog_action_row(top_margin=2)
        actions.addStretch(1)
        cancel = compact_button(_pt("dialog.cancel"), width=88)
        cancel.clicked.connect(self.reject)
        self._create = compact_button(
            _pt("excel.create") if mode == "create" else _pt("dialog.ok"), primary=True, width=96)
        self._create.clicked.connect(self._submit)
        actions.addWidget(cancel)
        actions.addWidget(self._create)
        root.addWidget(actions_host)

    def _add_files(self) -> None:
        files = get_open_file_names(self, _pt("excel.pick_title"), "", _pt("excel.file_filter"))
        self._stage_paths([Path(f) for f in files])

    def _stage_paths(self, paths: list[Path]) -> None:
        added = 0
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            row = _StagedRow(path, self._remove_row)
            self._rows.append(row)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            added += 1
        if added and self._mode == "create" and not self._name.text().strip():
            self._name.setText(self._rows[0].path.stem)     # prefill from the first file
        self._empty.setVisible(not self._rows)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTS for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self._stage_paths(paths)
        event.acceptProposedAction()

    def _remove_row(self, row: _StagedRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()
        self._empty.setVisible(not self._rows)

    def _submit(self) -> None:
        if self._mode == "create":
            name = self._name.text().strip()
            if not name:
                dialog_warn(self, _pt("excel.new_title"), _pt("settings.err.conn_name"))
                return
            if not is_valid_collection_name(name):
                dialog_warn(self, _pt("excel.new_title"), _pt("excel.err.bad_name"))
                return
            if name.lower() in self._existing:
                dialog_warn(self, _pt("excel.new_title"), _pt("excel.err.name_taken", name=name))
                return
        if not self._rows:
            dialog_warn(self, _pt("excel.new_title"), _pt("excel.no_files"))
            return
        seen: set[str] = set()
        for row in self._rows:
            label = row.name()
            if not label:
                dialog_warn(self, _pt("excel.new_title"), _pt("excel.err.empty_name"))
                return
            if label.lower() in seen:
                dialog_warn(self, _pt("excel.new_title"), _pt("excel.err.dup_name", name=label))
                return
            seen.add(label.lower())
        self.accept()

    def result_value(self) -> tuple[str, list[ImportSpec]]:
        return (
            self._name.text().strip(),
            [ImportSpec(row.path, name=row.name(), header_anchors=(row.header_anchors or None),
                        sheets=row.sheets)
             for row in self._rows],
        )


def new_collection(parent, existing_names: set[str]) -> tuple[str, list[ImportSpec]] | None:
    dialog = NewCollectionDialog(parent, existing_names)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.result_value()


def add_collection_files(parent) -> list[ImportSpec] | None:
    """Staging dialog for adding files to an existing collection — same rename + header-pick
    UI as creation, minus the connection-name field."""
    dialog = NewCollectionDialog(parent, set(), mode="add")
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.result_value()[1]
