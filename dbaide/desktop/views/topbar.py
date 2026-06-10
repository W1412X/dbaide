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
from dbaide.desktop.components.menu import MenuButton, PillSelect
from dbaide.desktop.platform_ui import configure_chrome_button, label_for_chrome_button
from dbaide.desktop.theme import Theme


class ModeSwitch(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("modeSwitch")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self._buttons: list[QToolButton] = []
        self._current = -1
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._row = row
        self._apply_style()

    def addTab(self, icon, text: str = "") -> int:  # noqa: N802 - QTabBar-compatible API
        index = len(self._buttons)
        btn = QToolButton(self)
        btn.setObjectName("modeSwitchButton")
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setIcon(icon)
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(26)
        configure_chrome_button(btn)
        label = label_for_chrome_button(text, icon_only=not bool(text))
        if label:
            btn.setText(label)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if text:
            btn.setToolTip(text)
        btn.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
        self._buttons.append(btn)
        self._row.addWidget(btn)
        if self._current < 0:
            self.setCurrentIndex(0, emit=False)
        self._resize()
        return index

    def _resize(self) -> None:
        if label_for_chrome_button("x", icon_only=False):
            total = max(148, sum(max(72, btn.sizeHint().width() + 8) for btn in self._buttons))
        else:
            total = max(72, sum(btn.sizeHint().width() + 8 for btn in self._buttons))
        self.setFixedWidth(total)

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
                border-radius: {Theme.RADIUS_LG}px;
            }}
            QToolButton#modeSwitchButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: {Theme.RADIUS_MD}px;
                padding: 0 8px;
                margin: 0;
                color: {Theme.TEXT_2};
                font-size: 11px;
                font-weight: 600;
            }}
            QToolButton#modeSwitchButton:hover {{
                background: {Theme.PANEL_2};
            }}
            QToolButton#modeSwitchButton:checked {{
                background: {Theme.PANEL_2};
                border: 1px solid {Theme.BORDER};
                color: {Theme.TEXT};
            }}
            """
        )


class TopBar(QWidget):
    connection_changed = pyqtSignal(str)
    refresh = pyqtSignal()
    build_assets = pyqtSignal()
    settings = pyqtSignal()
    joins_requested = pyqtSignal()
    sync_schema_requested = pyqtSignal()
    copy_conversation_requested = pyqtSignal()
    export_debug_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setStyleSheet(f"background:{Theme.BG}; border:none;")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(6)

        from dbaide.desktop.components.icons import app_logo_pixmap
        logo_pm = app_logo_pixmap(20)
        if logo_pm is not None:
            logo = QLabel()
            logo.setPixmap(logo_pm)
            logo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            logo.setStyleSheet("padding:0 2px 0 0;")
            row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        brand = QLabel("DBAide")
        brand.setStyleSheet(
            "font-size:15px;font-weight:700;letter-spacing:0.3px;"
            f"color:{Theme.TEXT};padding:0 8px 0 6px;"
        )
        brand.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(brand)
        row.addSpacing(8)

        from dbaide.i18n import t

        # Pill selectors — one unified control (label + chevron), no legacy QComboBox
        # drop-down subcontrol box that showed black corners on macOS.
        self.connection = PillSelect(t("topbar.connection"), max_width=240, soft=True)
        self.connection.value_changed.connect(self._emit_connection)
        configure_chrome_button(self.connection)
        row.addWidget(self.connection)

        row.addStretch(1)

        self.mode_tabs = ModeSwitch()
        row.addWidget(self.mode_tabs)

        self.status = StatusBadge("Idle", "idle")
        row.addWidget(self.status)

        self.menu = MenuButton(
            icon=more_icon(color=Theme.TEXT_2), tooltip=t("topbar.settings"), icon_only=True
        )
        configure_chrome_button(self.menu)
        self.menu.add_action(t("topbar.build"), self.build_assets.emit)
        self.menu.add_action(t("menu.sync_schema"), self.sync_schema_requested.emit)
        self.menu.add_action(t("topbar.refresh"), self.refresh.emit)
        self.menu.add_separator()
        self.menu.add_action(t("menu.joins"), self.joins_requested.emit)
        self.menu.add_action(t("panel.copy_conversation"), self.copy_conversation_requested.emit)
        self.menu.add_action(t("menu.export_debug"), self.export_debug_requested.emit)
        self.menu.add_separator()
        self.menu.add_action(t("topbar.settings") + "…", self.settings.emit)
        row.addWidget(self.menu)

    def _emit_connection(self, value: str) -> None:
        self.connection_changed.emit(str(value or ""))

    def set_connections(self, items: list[dict[str, Any]], default: str = "") -> None:
        options = [(f"{item['name']} · {item['type']}", str(item["name"])) for item in items]
        self.connection.blockSignals(True)
        self.connection.set_options(options)
        if default:
            self.connection.set_value(default)
        elif options:
            self.connection.set_value(options[0][1])
        self.connection.blockSignals(False)

    def set_asset_status(self, status: str) -> None:
        from dbaide.i18n import t
        mapping = {
            "ready": (t("topbar.status.ready"), "ready"),
            "missing": (t("topbar.status.no_assets"), "missing"),
            "building": (t("topbar.status.building"), "building"),
        }
        text, state = mapping.get(status, (t("topbar.status.idle"), "idle"))
        self.status.set_state(text, state)

    def set_global_status(self, text: str, state: str = "idle") -> None:
        self.status.set_state(text, state)
