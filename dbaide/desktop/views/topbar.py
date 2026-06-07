from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QToolButton,
    QSizePolicy,
    QWidget,
)

from dbaide.desktop.components.base import StatusBadge
from dbaide.desktop.components.icons import more_icon
from dbaide.desktop.components.inputs import DropdownCombo
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.theme import Theme


class ModeSwitch(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("modeSwitch")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(78, 30)
        self._buttons: list[QToolButton] = []
        self._current = -1
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(0)
        self._row = row
        self._apply_style()

    def addTab(self, icon, text: str = "") -> int:  # noqa: N802 - QTabBar-compatible API
        index = len(self._buttons)
        btn = QToolButton(self)
        btn.setObjectName("modeSwitchButton")
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setIcon(icon)
        btn.setIconSize(QSize(16, 16))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(36, 26)
        if text:
            btn.setToolTip(text)
        btn.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
        self._buttons.append(btn)
        self._row.addWidget(btn)
        if self._current < 0:
            self.setCurrentIndex(0, emit=False)
        return index

    def setTabToolTip(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= index < len(self._buttons):
            self._buttons[index].setToolTip(text)

    def tabToolTip(self, index: int) -> str:  # noqa: N802
        if 0 <= index < len(self._buttons):
            return self._buttons[index].toolTip()
        return ""

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def setCurrentIndex(self, index: int, *, emit: bool = True) -> None:  # noqa: N802
        if not (0 <= index < len(self._buttons)) or index == self._current:
            return
        self._current = index
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        if emit:
            self.currentChanged.emit(index)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#modeSwitch {{
                background: transparent;
                border: none;
                border-radius: 9px;
            }}
            QToolButton#modeSwitchButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 0;
                margin: 0;
            }}
            QToolButton#modeSwitchButton:hover {{
                background: {Theme.PANEL_2};
            }}
            QToolButton#modeSwitchButton:checked {{
                background: {Theme.PANEL_2};
                border: 1px solid {Theme.BORDER};
            }}
            """
        )


class TopBar(QWidget):
    connection_changed = pyqtSignal(str)
    database_changed = pyqtSignal(str)
    refresh = pyqtSignal()
    build_assets = pyqtSignal()
    settings = pyqtSignal()
    joins_requested = pyqtSignal()
    sync_schema_requested = pyqtSignal()
    copy_conversation_requested = pyqtSignal()
    new_query_requested = pyqtSignal()
    new_conn_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setStyleSheet(f"background:{Theme.BG}; border:none;")
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
        row.addSpacing(8)

        from dbaide.i18n import t

        # New query / build assets / new connection are reachable via ⌘T + the
        # Workbench "+ New SQL" button, and Build / Settings in the overflow menu —
        # so the topbar stays clean: brand · connection · database … mode · status · ⋯.

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

        self.mode_tabs = ModeSwitch()
        row.addWidget(self.mode_tabs)

        # Status + overflow menu
        self.status = StatusBadge("Idle", "idle")
        row.addWidget(self.status)

        self.menu = MenuButton(
            icon=more_icon(color=Theme.TEXT_2), tooltip=t("topbar.settings"), icon_only=True
        )
        self.menu.add_action(t("topbar.build"), self.build_assets.emit)
        self.menu.add_action(t("menu.sync_schema"), self.sync_schema_requested.emit)
        self.menu.add_action(t("topbar.refresh"), self.refresh.emit)
        self.menu.add_separator()
        self.menu.add_action(t("menu.joins"), self.joins_requested.emit)
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
