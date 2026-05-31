"""Panel header: segmented view switcher + compact action icons."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QTabBar, QWidget

from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import clock_icon, link_icon, more_icon
from dbaide.desktop.components.menu import MenuButton


class PanelHeader(QWidget):
    """Codex-style row: [Inspector | Trace | Plan] ··· [history] [joins] [more]."""

    tab_changed = pyqtSignal(int)
    history_clicked = pyqtSignal()
    joins_clicked = pyqtSignal()
    copy_trace_requested = pyqtSignal()
    clear_trace_requested = pyqtSignal()
    clear_conversation_requested = pyqtSignal()

    def __init__(self, tab_labels: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.tabbar = QTabBar()
        self.tabbar.setProperty("segmented", True)
        self.tabbar.setProperty("panelTabs", True)
        self.tabbar.setDrawBase(False)
        self.tabbar.setExpanding(False)
        self.tabbar.setUsesScrollButtons(False)
        self.tabbar.setDocumentMode(True)
        for label in tab_labels:
            self.tabbar.addTab(label)
        self.tabbar.currentChanged.connect(self.tab_changed.emit)
        row.addWidget(self.tabbar)
        row.addStretch(1)

        actions = QWidget()
        actions.setFixedHeight(28)
        action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(2)
        self._btn_history = IconToolButton(clock_icon(), "Workflow history")
        self._btn_joins = IconToolButton(link_icon(), "Saved joins")
        self._btn_history.clicked.connect(self.history_clicked.emit)
        self._btn_joins.clicked.connect(self.joins_clicked.emit)
        action_row.addWidget(self._btn_history)
        action_row.addWidget(self._btn_joins)
        self._menu = MenuButton(icon=more_icon(), tooltip="Panel actions", icon_only=True)
        self._menu.add_action("Copy Trace", self.copy_trace_requested.emit)
        self._menu.add_action("Clear Trace", self.clear_trace_requested.emit)
        self._menu.add_action("Clear Conversation", self.clear_conversation_requested.emit)
        action_row.addWidget(self._menu)
        row.addWidget(actions, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_current_tab(self, index: int) -> None:
        if 0 <= index < self.tabbar.count():
            self.tabbar.setCurrentIndex(index)

    def current_tab(self) -> int:
        return self.tabbar.currentIndex()
