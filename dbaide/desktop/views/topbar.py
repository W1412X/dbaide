from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from dbaide.desktop.components.base import StatusBadge
from dbaide.desktop.components.inputs import DropdownCombo
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.theme import Theme


class TopBar(QWidget):
    connection_changed = pyqtSignal(str)
    database_changed = pyqtSignal(str)
    refresh = pyqtSignal()
    build_assets = pyqtSignal()
    settings = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(f"background:{Theme.BG}; border-bottom:1px solid {Theme.BORDER_SOFT};")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(10)

        brand = QLabel("DBAide")
        brand.setFixedWidth(72)
        brand.setStyleSheet("font-size:17px;font-weight:900;padding:0;margin:0;")
        brand.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(brand)

        self.connection = DropdownCombo(max_visible=8)
        self.connection.currentIndexChanged.connect(self._emit_connection)
        self.database = DropdownCombo(max_visible=10)
        self.database.currentIndexChanged.connect(self._emit_database)
        row.addWidget(self.connection, 1)
        row.addWidget(self.database, 1)
        row.addStretch(1)

        self.status = StatusBadge("Idle", "idle")
        row.addWidget(self.status)

        self.menu = MenuButton("⋯")
        self.menu.setFixedSize(36, 32)
        self.menu.add_action("Build Assets", self.build_assets.emit)
        self.menu.add_action("Refresh", self.refresh.emit)
        self.menu.add_separator()
        self.menu.add_action("Settings…", self.settings.emit)
        row.addWidget(self.menu)

    def _emit_connection(self, _index: int) -> None:
        self.connection_changed.emit(self.connection.currentText())

    def _emit_database(self, _index: int) -> None:
        self.database_changed.emit(self.database.currentText())

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
            "partial": ("Partial", "warning"),
        }
        text, state = mapping.get(status, ("Idle", "idle"))
        self.status.set_state(text, state)

    def set_global_status(self, text: str, state: str = "idle") -> None:
        self.status.set_state(text, state)
