from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from dbaide.desktop.components.base import StatusBadge
from dbaide.desktop.components.icons import more_icon
from dbaide.desktop.components.inputs import DropdownCombo
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.theme import Theme


def _sep() -> QFrame:
    """Vertical separator for the topbar."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setFixedHeight(20)
    f.setStyleSheet(f"background: {Theme.BORDER_SOFT}; border: none;")
    return f


class TopBar(QWidget):
    connection_changed = pyqtSignal(str)
    database_changed = pyqtSignal(str)
    refresh = pyqtSignal()
    build_assets = pyqtSignal()
    settings = pyqtSignal()
    joins_requested = pyqtSignal()
    notes_requested = pyqtSignal()
    copy_conversation_requested = pyqtSignal()
    new_query_requested = pyqtSignal()
    new_conn_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setStyleSheet(
            f"background:{Theme.BG}; border-bottom:1px solid {Theme.BORDER_SOFT};"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(4)

        # Brand
        brand = QLabel("DBAide")
        brand.setStyleSheet(
            "font-size:15px;font-weight:700;letter-spacing:0.3px;"
            f"color:{Theme.TEXT};padding:0 8px 0 2px;"
        )
        brand.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(brand)
        row.addWidget(_sep())

        from dbaide.i18n import t

        # New query / build assets / new connection used to sit here as a cluttered
        # icon-button strip; they're reachable via ⌘T + the Workbench "+ New SQL"
        # button, and Build / Settings in the overflow menu — so the topbar stays
        # clean: brand · connection · database … status · ⋯.

        # Connection + database selectors
        self.connection = DropdownCombo(max_visible=8)
        self.connection.setProperty("soft", True)
        self.connection.currentIndexChanged.connect(self._emit_connection)
        self.database = DropdownCombo(max_visible=10)
        self.database.setProperty("soft", True)
        self.database.currentIndexChanged.connect(self._emit_database)
        row.addWidget(self.connection)
        row.addWidget(self.database)

        row.addStretch(1)

        # Status + panel toggle + overflow menu
        self.status = StatusBadge("Idle", "idle")
        row.addWidget(self.status)

        self.menu = MenuButton(
            icon=more_icon(color=Theme.TEXT_2), tooltip=t("topbar.settings"), icon_only=True
        )
        self.menu.add_action(t("topbar.build"), self.build_assets.emit)
        self.menu.add_action(t("topbar.refresh"), self.refresh.emit)
        self.menu.add_separator()
        self.menu.add_action(t("menu.joins"), self.joins_requested.emit)
        self.menu.add_action(t("menu.notes"), self.notes_requested.emit)
        self.menu.add_action(t("panel.copy_conversation"), self.copy_conversation_requested.emit)
        self.menu.add_separator()
        self.menu.add_action(t("topbar.settings") + "…", self.settings.emit)
        row.addWidget(self.menu)

    # ── signal helpers ────────────────────────────────────────────────────────

    def _emit_connection(self, _index: int) -> None:
        self.connection_changed.emit(self.connection.currentText())

    def _emit_database(self, _index: int) -> None:
        self.database_changed.emit(self.database.currentText())

    # ── setters ───────────────────────────────────────────────────────────────

    def set_connections(self, items: list[dict[str, Any]], default: str = "") -> None:
        self.connection.blockSignals(True)
        self.connection.clear()
        for item in items:
            label = f"{item['name']} · {item['type']}"
            self.connection.addItem(label, item["name"])
        if default:
            idx = self.connection.findData(default)
            if idx >= 0:
                self.connection.setCurrentIndex(idx)
        self.connection.blockSignals(False)

    def set_databases(self, names: list[str]) -> None:
        self.database.blockSignals(True)
        self.database.clear()
        self.database.addItem("Auto", "")
        for name in names:
            self.database.addItem(name, name)
        self.database.blockSignals(False)

    def set_asset_status(self, status: str) -> None:
        mapping = {
            "ready": ("Ready", "ready"),
            "missing": ("No assets", "missing"),
            "building": ("Building", "building"),
        }
        text, state = mapping.get(status, ("Idle", "idle"))
        self.status.set_state(text, state)

    def set_global_status(self, text: str, state: str = "idle") -> None:
        self.status.set_state(text, state)
