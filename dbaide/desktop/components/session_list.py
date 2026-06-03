"""Chat session list — the navigable list of conversation threads (会话).

A header with a "New chat" action over a list of sessions; each row shows the
session title and a muted subtitle (turn count · relative time). Right-click a row
to rename or delete it.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import SectionLabel
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import plus_icon
from dbaide.desktop.theme import Theme
from dbaide.i18n import t

_ID_ROLE = Qt.ItemDataRole.UserRole


def _relative_time(ts: float) -> str:
    if not ts:
        return ""
    delta = max(0.0, time.time() - float(ts))
    if delta < 60:
        return t("session.just_now")
    if delta < 3600:
        return t("session.minutes_ago", n=int(delta // 60))
    if delta < 86400:
        return t("session.hours_ago", n=int(delta // 3600))
    if delta < 7 * 86400:
        return t("session.days_ago", n=int(delta // 86400))
    return time.strftime("%b %d", time.localtime(ts))


_TITLE_FONT = QFont("Inter", 12, QFont.Weight.DemiBold)
_SUB_FONT = QFont("Inter", 10)
_TITLE_MAX_LINES = 2


class _SessionRow(QWidget):
    """Title (wraps to up to two lines so similar questions stay distinguishable)
    over a muted 'N turns · time' subtitle."""

    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(title)  # full title on hover, even when it wraps/clips
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(2)
        self._full_title = title
        self._title = QLabel(title)
        self._title.setFont(_TITLE_FONT)
        self._title.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Cap the visible title at two lines (a 3rd would just clip — rare for a title).
        self._title.setMaximumHeight(_TITLE_MAX_LINES * QFontMetrics(_TITLE_FONT).lineSpacing())
        sub = QLabel(subtitle)
        sub.setFont(_SUB_FONT)
        sub.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
        layout.addWidget(self._title)
        layout.addWidget(sub)

    def title(self) -> str:
        return self._full_title

    def resizeEvent(self, event) -> None:  # noqa: N802
        # Constrain the title to the row width so word-wrap actually wraps (an
        # unconstrained wrapping label clips to one line instead).
        super().resizeEvent(event)
        self._title.setFixedWidth(max(40, self.width() - 8))

    @staticmethod
    def height_for(title: str, *, content_width: int) -> int:
        """Row height that fits the title (1 or 2 lines, the true wrapped height)
        plus the subtitle."""
        tfm = QFontMetrics(_TITLE_FONT)
        w = max(60, content_width)
        wrapped = tfm.boundingRect(0, 0, w, 10000, int(Qt.TextFlag.TextWordWrap), title).height()
        title_h = min(wrapped, _TITLE_MAX_LINES * tfm.lineSpacing())
        sub_h = QFontMetrics(_SUB_FONT).lineSpacing()
        return title_h + sub_h + 16  # margins (5+5) + spacing (2) + a hair


class SessionList(QWidget):
    new_requested = pyqtSignal()
    selected = pyqtSignal(str)             # session_id
    rename_requested = pyqtSignal(str, str)  # session_id, new_title
    delete_requested = pyqtSignal(str)       # session_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(SectionLabel(t("session.chats")))
        header.addStretch(1)
        self._new_btn = IconToolButton(plus_icon(color=Theme.TEXT_2), t("session.new"))
        self._new_btn.clicked.connect(self.new_requested.emit)
        header.addWidget(self._new_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.list.setWordWrap(True)  # so the empty-state hint wraps instead of clipping
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._on_click)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        layout.addWidget(self.list, 1)

        self._current = ""
        self._empty: QListWidgetItem | None = None

    def load(self, sessions: list[dict[str, Any]]) -> None:
        self.list.clear()
        if not sessions:
            item = QListWidgetItem(t("session.empty"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(self.palette().color(self.foregroundRole()))
            from PyQt6.QtGui import QColor
            item.setForeground(QColor(Theme.MUTED))
            self.list.addItem(item)
            return
        from dbaide.history.session_store import DEFAULT_TITLE
        for s in sessions:
            sid = str(s.get("session_id") or "")
            title = str(s.get("title") or "")
            if not title or title == DEFAULT_TITLE:
                title = t("session.new")
            n = int(s.get("turn_count") or 0)
            when = _relative_time(float(s.get("updated_at") or s.get("created_at") or 0))
            bits = [t("session.turns_one") if n == 1 else t("session.turns_many", n=n)]
            if when:
                bits.append(when)
            row = _SessionRow(title, " · ".join(bits))
            item = QListWidgetItem()
            item.setData(_ID_ROLE, sid)
            # Width available to the title inside the row: list viewport minus the
            # item padding (8+8) and the row margins (4+4). Fall back to a typical
            # sidebar width before the list has been laid out.
            vw = self.list.viewport().width()
            content_w = (vw if vw > 40 else 232) - 24
            item.setSizeHint(QSize(0, _SessionRow.height_for(title, content_width=content_w)))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

    def resizeEvent(self, event) -> None:  # noqa: N802
        # Recompute row heights for the current width (load may run before layout,
        # and the sidebar splitter can change the width).
        super().resizeEvent(event)
        vw = self.list.viewport().width()
        if vw <= 40:
            return
        content_w = vw - 24
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = self.list.itemWidget(it)
            if isinstance(w, _SessionRow):
                it.setSizeHint(QSize(0, _SessionRow.height_for(w.title(), content_width=content_w)))
        self.set_current(self._current)

    def set_current(self, session_id: str) -> None:
        self._current = str(session_id or "")
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is not None and item.data(_ID_ROLE) == self._current and self._current:
                self.list.setCurrentItem(item)
                return
        self.list.clearSelection()

    def _on_click(self, item: QListWidgetItem) -> None:
        sid = item.data(_ID_ROLE)
        if sid:
            self._current = str(sid)
            self.selected.emit(str(sid))

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or not item.data(_ID_ROLE):
            return
        sid = str(item.data(_ID_ROLE))
        menu = QMenu(self)
        from dbaide.desktop.components.menu import _style_menu
        _style_menu(menu)
        rename = QAction(t("session.rename"), menu)
        rename.triggered.connect(lambda: self._rename(sid))
        delete = QAction(t("session.delete"), menu)
        delete.triggered.connect(lambda: self.delete_requested.emit(sid))
        menu.addAction(rename)
        menu.addAction(delete)
        menu.exec(self.list.mapToGlobal(pos))

    def _rename(self, session_id: str) -> None:
        current = ""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is not None and it.data(_ID_ROLE) == session_id:
                w = self.list.itemWidget(it)
                current = w.title() if isinstance(w, _SessionRow) else ""
                break
        title, ok = QInputDialog.getText(self, t("session.rename_title"), t("session.title_label"), text=current)
        if ok and title.strip():
            self.rename_requested.emit(session_id, title.strip())
