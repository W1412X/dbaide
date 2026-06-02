"""Panel header: segmented view switcher + compact action icons."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QTabBar, QWidget

from dbaide.desktop.components.icons import more_icon
from dbaide.desktop.components.menu import MenuButton


class PanelHeader(QWidget):
    """Codex-style row: [Inspector | Trace | Plan] ··· [history] [joins] [more]."""

    tab_changed = pyqtSignal(int)
    history_clicked = pyqtSignal()
    joins_clicked = pyqtSignal()
    copy_trace_requested = pyqtSignal()
    copy_conversation_requested = pyqtSignal()
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

        # One overflow menu holds the secondary navigation/management and the
        # contextual trace actions — keeps the header to "[views] · [⋯]".
        from dbaide.i18n import t
        self._menu = MenuButton(icon=more_icon(), tooltip="More", icon_only=True)
        # Query history is now the Chats list in the sidebar (sessions group turns);
        # the old per-workflow History dialog is superseded, so it's no longer here.
        self._menu.add_action(t("menu.joins"), self.joins_clicked.emit)
        self._menu.add_separator()
        self._menu.add_action(t("panel.copy_trace"), self.copy_trace_requested.emit)
        self._menu.add_action(t("panel.copy_conversation"), self.copy_conversation_requested.emit)
        self._menu.add_action(t("panel.clear_trace"), self.clear_trace_requested.emit)
        self._menu.add_action(t("panel.clear_conversation"), self.clear_conversation_requested.emit)
        row.addWidget(self._menu, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_current_tab(self, index: int) -> None:
        if 0 <= index < self.tabbar.count():
            self.tabbar.setCurrentIndex(index)

    def current_tab(self) -> int:
        return self.tabbar.currentIndex()
