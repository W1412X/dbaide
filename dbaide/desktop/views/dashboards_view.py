"""Dashboards mode: a gallery of saved AI interactive dashboards + the studio.

This replaces the old static "basic board". Saved dashboards (built from the Ask
conversation) are listed here as cards; opening one shows it in the studio (view +
chat-refine). The basic pinned-tile board is retired.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.dialogs.message_dialog import confirm as dialog_confirm
from dbaide.desktop.theme import Theme
from dbaide.desktop.views.parametric_dashboard import ParametricDashboardStudio
from dbaide.i18n import t as _t


class _Card(QFrame):
    def __init__(self, app: dict[str, Any], on_open: Callable[[str], None],
                 on_delete: Callable[[str, str], None], parent=None) -> None:
        super().__init__(parent)
        aid = str(app.get("id") or "")
        self.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:10px; }} QFrame:hover {{ border-color:{Theme.ACCENT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 14, 14)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        name = QLabel(str(app.get("name") or _t("app.window_title")))
        name.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        name.setStyleSheet(f"color:{Theme.TEXT}; background:transparent; border:none;")
        col.addWidget(name)
        meta_bits = [_t("dash.card_charts", n=int(app.get("charts") or 0))]
        if app.get("connection_name"):
            meta_bits.append(str(app["connection_name"]))
        if app.get("updated_at"):
            meta_bits.append(str(app["updated_at"])[:10])
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent; border:none;")
        col.addWidget(meta)
        lay.addLayout(col, 1)
        open_btn = compact_button(_t("dash.open"), primary=True, width=72)
        open_btn.clicked.connect(lambda: on_open(aid))
        lay.addWidget(open_btn)
        del_btn = compact_button(_t("dash.delete"), width=64)
        del_btn.clicked.connect(lambda: on_delete(aid, str(app.get("name") or "")))
        lay.addWidget(del_btn)


class _Gallery(QWidget):
    def __init__(self, service, on_open: Callable[[str], None], parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._on_open = on_open
        self.setStyleSheet(f"background:{Theme.BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        title = QLabel(_t("dash.gallery_title"))
        title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;")
        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(10)
        self._list.addStretch(1)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

    def reload(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            apps = self._service.dispatch("list_dashboard_apps", {}).get("apps", [])
        except Exception:  # noqa: BLE001
            apps = []
        if not apps:
            empty = QLabel(_t("dash.empty"))
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color:{Theme.MUTED}; font-size:13px; padding:40px;")
            self._list.addWidget(empty)
        else:
            for app in apps:
                self._list.addWidget(_Card(app, self._on_open, self._delete))
        self._list.addStretch(1)

    def _delete(self, app_id: str, name: str) -> None:
        if not dialog_confirm(self, _t("dash.gallery_title"), _t("dash.delete_confirm", name=name)):
            return
        try:
            self._service.dispatch("delete_dashboard_app", {"id": app_id})
        except Exception:  # noqa: BLE001
            pass
        self.reload()


class DashboardsView(QWidget):
    """Gallery ↔ tabbed studios. Opening a saved dashboard adds a VIEW-only tab;
    generating a new one adds an edit/generate tab. Same reload/shutdown API as the
    old DashboardTab, plus open_generate() for the chat 'build dashboard' action."""

    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self.setStyleSheet(f"background:{Theme.BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._gallery = _Gallery(service, on_open=self._open)
        self._stack.addWidget(self._gallery)            # 0

        # Opened dashboards live as tabs; a corner "Boards" button returns to the gallery.
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            f"QTabBar::tab {{ background:{Theme.PANEL}; color:{Theme.TEXT_2}; border:1px solid {Theme.BORDER_SOFT};"
            f" padding:6px 12px; margin-right:2px; border-top-left-radius:6px; border-top-right-radius:6px; }}"
            f" QTabBar::tab:selected {{ background:{Theme.BG}; color:{Theme.TEXT}; border-bottom-color:{Theme.BG}; }}"
            f" QTabWidget::pane {{ border:none; }}")
        self._tabs.tabCloseRequested.connect(self._close_tab)
        home = compact_button(_t("dash.boards_home"), width=92)
        home.clicked.connect(self._show_gallery)
        self._tabs.setCornerWidget(home, Qt.Corner.TopLeftCorner)
        self._stack.addWidget(self._tabs)               # 1
        root.addWidget(self._stack)

    # -- API expected by main_window (mirrors the old DashboardTab) -----------

    def reload(self) -> None:
        if self._stack.currentIndex() == 0:
            self._gallery.reload()

    def shutdown(self) -> None:
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if hasattr(w, "shutdown"):
                w.shutdown()

    def open_generate(self, *, name: str, connection_name: str,
                      context: list[dict], instruction: str) -> ParametricDashboardStudio:
        """Open a NEW tab that generates a dashboard (edit mode) — used by the chat
        'build dashboard' action."""
        studio = ParametricDashboardStudio(self._service)
        i = self._tabs.addTab(studio, name or _t("dash.untitled"))
        studio.titleChanged.connect(lambda t, s=studio: self._relabel(s, t))
        studio.start(name=name, connection_name=connection_name, context=context, instruction=instruction)
        self._tabs.setCurrentIndex(i)
        self._stack.setCurrentIndex(1)
        return studio

    # -- navigation -----------------------------------------------------------

    def _open(self, app_id: str) -> None:
        existing = self._tab_for_app(app_id) if app_id else -1
        if existing >= 0:
            self._tabs.setCurrentIndex(existing)
        else:
            studio = ParametricDashboardStudio(self._service)
            studio.open_existing(app_id)              # view-only
            i = self._tabs.addTab(studio, studio.title_text() or _t("dash.untitled"))
            studio.titleChanged.connect(lambda t, s=studio: self._relabel(s, t))
            self._tabs.setCurrentIndex(i)
        self._stack.setCurrentIndex(1)

    def _tab_for_app(self, app_id: str) -> int:
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if hasattr(w, "app_id") and w.app_id() == app_id:
                return i
        return -1

    def _relabel(self, studio: QWidget, title: str) -> None:
        i = self._tabs.indexOf(studio)
        if i >= 0:
            self._tabs.setTabText(i, title or _t("dash.untitled"))

    def _close_tab(self, index: int) -> None:
        w = self._tabs.widget(index)
        if w is not None:
            if hasattr(w, "shutdown"):
                w.shutdown()
            self._tabs.removeTab(index)
            w.deleteLater()
        if self._tabs.count() == 0:
            self._show_gallery()

    def _show_gallery(self) -> None:
        self._stack.setCurrentIndex(0)
        self._gallery.reload()
