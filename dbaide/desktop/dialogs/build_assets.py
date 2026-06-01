"""Database selection dialog for partial asset builds."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.theme import Theme


class BuildAssetsDialog(QDialog):
    def __init__(
        self,
        *,
        connection_name: str,
        databases: list[dict[str, object]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Build Assets")
        self.setModal(True)
        self.resize(440, 420)
        self.setMinimumSize(360, 280)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"Select databases to build for `{connection_name}`")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 14px; font-weight: 600;")
        root.addWidget(title)

        hint = QLabel("Unchecked databases keep their existing offline assets.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(6)

        self._checks: list[QCheckBox] = []
        for entry in databases:
            name = str(entry.get("name") or "")
            if not name:
                continue
            label = name
            if entry.get("has_assets"):
                label = f"{name}  ·  built"
            box = QCheckBox(label)
            box.setChecked(True)
            box.setProperty("db_name", name)
            box.setStyleSheet(f"color: {Theme.TEXT_2}; padding: 4px 0;")
            self._checks.append(box)
            inner_layout.addWidget(box)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        select_all = compact_button("Select all", width=96)
        select_none = compact_button("Select none", width=104)
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none.clicked.connect(lambda: self._set_all(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        root.addLayout(select_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        cancel = compact_button("Cancel", width=88)
        build = compact_button("Build", primary=True, width=88)
        cancel.clicked.connect(self.reject)
        build.clicked.connect(self._accept_if_valid)
        self._build_btn = build
        actions.addWidget(cancel)
        actions.addWidget(build)
        root.addLayout(actions)

        for box in self._checks:
            box.toggled.connect(self._sync_build_enabled)
        self._sync_build_enabled()

    def _set_all(self, checked: bool) -> None:
        for box in self._checks:
            box.setChecked(checked)

    def _sync_build_enabled(self, *_args) -> None:
        self._build_btn.setEnabled(any(box.isChecked() for box in self._checks))

    def _accept_if_valid(self) -> None:
        if self.selected_databases():
            self.accept()

    def selected_databases(self) -> list[str]:
        return [str(box.property("db_name") or box.text()) for box in self._checks if box.isChecked()]
