"""Database selection dialog for partial asset builds."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import Combo
from dbaide.desktop.theme import Theme


class BuildAssetsDialog(QDialog):
    def __init__(
        self,
        *,
        connection_name: str,
        databases: list[dict[str, object]],
        load_profile: str = "production",
        default_profile_mode: str = "light",
        default_max_workers: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self.setWindowTitle(t("build.title"))
        self.setModal(True)
        self.resize(440, 520)
        self.setMinimumSize(360, 360)
        self.setStyleSheet(f"QDialog {{ background: {Theme.BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(t("build.select_for", conn=connection_name))
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 14px; font-weight: 600;")
        root.addWidget(title)

        hint = QLabel(t("build.hint"))
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
                label = t("build.db_built", name=name)
            box = QCheckBox(label)
            box.setChecked(True)
            box.setProperty("db_name", name)
            box.setStyleSheet(f"color: {Theme.TEXT_2}; padding: 4px 0;")
            self._checks.append(box)
            inner_layout.addWidget(box)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        # Size to the checkbox list (capped, scrolls when there are many DBs) rather
        # than greedily filling the dialog — otherwise a couple of DBs leave a large
        # gap between the list and the controls below.
        scroll.setMaximumHeight(180)
        root.addWidget(scroll)

        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        select_all = compact_button(t("build.select_all"), width=96)
        select_none = compact_button(t("build.select_none"), width=104)
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none.clicked.connect(lambda: self._set_all(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        root.addLayout(select_row)

        # ── Resource options (Profile depth / concurrency / total timeout) ──
        options = QFormLayout()
        options.setSpacing(8)

        self._profile = Combo()
        self._profile.addItems(["none", "light", "auto", "all"])
        idx = max(0, self._profile.findText(str(default_profile_mode or "light")))
        self._profile.setCurrentIndex(idx)
        options.addRow(t("build.profile_depth"), self._profile)

        self._workers = QSpinBox()
        self._workers.setRange(1, 32)
        self._workers.setValue(max(1, int(default_max_workers or 1)))
        options.addRow(t("build.concurrency"), self._workers)

        self._timeout = QSpinBox()
        self._timeout.setRange(0, 7200)
        self._timeout.setValue(3600)
        self._timeout.setSuffix(t("build.time_suffix"))
        options.addRow(t("build.time_budget"), self._timeout)

        profile_hint = QLabel(t("build.profile_note", profile=load_profile))
        profile_hint.setWordWrap(True)
        profile_hint.setProperty("muted", True)
        options.addRow(profile_hint)
        root.addLayout(options)

        # Pool any extra height here so the action row stays pinned to the bottom.
        root.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        cancel = compact_button(t("btn.cancel"), width=88)
        build = compact_button(t("btn.build"), primary=True, width=88)
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

    def build_options(self) -> dict[str, object]:
        return {
            "profile_mode": self._profile.currentText(),
            "max_workers": int(self._workers.value()),
            "timeout": int(self._timeout.value()),
        }
