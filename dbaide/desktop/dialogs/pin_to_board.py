"""Pin one or more charts from an answer onto a dashboard.

Dumb dialog: the caller supplies the answer's charts and the existing boards; the
dialog returns which charts to pin and the target board (existing id or a new
name). The caller (main window) does the actual ``pin_chart`` service calls.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
)

from dbaide.desktop.components.inputs import (
    STANDARD_FIELD_HEIGHT,
    configure_compact_field,
    dialog_action_row,
)
from dbaide.desktop.components.base import compact_button
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.window_chrome import ChromeDialog
from dbaide.i18n import t as _t


class PinToBoardDialog(ChromeDialog):
    def __init__(self, parent, charts: list[dict[str, Any]], boards: list[dict[str, Any]]) -> None:
        super().__init__(parent)
        self._charts = [c for c in (charts or []) if isinstance(c, dict)]
        self._boards = [b for b in (boards or []) if isinstance(b, dict)]
        self.setWindowTitle(_t("board.pin_title"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        heading = QLabel(_t("board.pin_title"))
        heading.setStyleSheet(f"color:{Theme.TEXT}; font-size:15px; font-weight:700; background:transparent;")
        root.addWidget(heading)

        # which charts -------------------------------------------------------
        self._chart_boxes: list[QCheckBox] = []
        if len(self._charts) > 1:
            lbl = QLabel(_t("board.pin_pick_charts"))
            lbl.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; background:transparent;")
            root.addWidget(lbl)
        for chart in self._charts:
            title = str(chart.get("title") or _t("conversation.chart")).strip()
            box = QCheckBox(title)
            box.setChecked(True)
            box.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
            if len(self._charts) == 1:
                box.setVisible(False)
            self._chart_boxes.append(box)
            root.addWidget(box)

        # target board -------------------------------------------------------
        board_lbl = QLabel(_t("board.pin_target"))
        board_lbl.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; font-weight:500; background:transparent;")
        root.addWidget(board_lbl)

        self._mode = QButtonGroup(self)
        self._use_existing = QRadioButton(_t("board.pin_existing"))
        self._use_new = QRadioButton(_t("board.pin_new"))
        self._mode.addButton(self._use_existing, 0)
        self._mode.addButton(self._use_new, 1)
        self._use_existing.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        self._use_new.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")

        self._existing = QComboBox()
        for board in self._boards:
            self._existing.addItem(str(board.get("name") or ""), str(board.get("id") or ""))
        configure_compact_field(self._existing, height=STANDARD_FIELD_HEIGHT)

        self._new_name = QLineEdit()
        self._new_name.setPlaceholderText(_t("board.pin_new_ph"))
        configure_compact_field(self._new_name, height=STANDARD_FIELD_HEIGHT)

        if self._boards:
            root.addWidget(self._use_existing)
            root.addWidget(self._existing)
        root.addWidget(self._use_new)
        root.addWidget(self._new_name)

        self._use_existing.toggled.connect(self._sync_mode)
        self._use_new.toggled.connect(self._sync_mode)
        (self._use_existing if self._boards else self._use_new).setChecked(True)
        self._sync_mode()

        actions_host, actions = dialog_action_row(top_margin=2)
        actions.addStretch(1)
        cancel = compact_button(_t("dialog.cancel"), width=88)
        cancel.clicked.connect(self.reject)
        self._ok = compact_button(_t("board.pin_confirm"), primary=True, width=110)
        self._ok.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(self._ok)
        root.addWidget(actions_host)

    def _sync_mode(self) -> None:
        existing = self._use_existing.isChecked()
        self._existing.setEnabled(existing)
        self._new_name.setEnabled(not existing)

    def selected_charts(self) -> list[dict[str, Any]]:
        return [c for c, box in zip(self._charts, self._chart_boxes) if box.isChecked()]

    def target(self) -> tuple[str, str]:
        """Return (dashboard_id, dashboard_name); exactly one is non-empty."""
        if self._use_existing.isChecked() and self._existing.count():
            return (str(self._existing.currentData() or ""), "")
        return ("", self._new_name.text().strip())


def pin_charts(parent, charts: list[dict[str, Any]], boards: list[dict[str, Any]]):
    """Show the dialog; return ``(charts, dashboard_id, dashboard_name)`` or None."""
    dlg = PinToBoardDialog(parent, charts, boards)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    picked = dlg.selected_charts()
    if not picked:
        return None
    dash_id, dash_name = dlg.target()
    if not dash_id and not dash_name:
        return None
    return (picked, dash_id, dash_name)
