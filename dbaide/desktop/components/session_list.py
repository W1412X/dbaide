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
_SUB_FONT = QFont("Inter", 9)
# Width the spinner + its gap reserve at the right of the title (so the elided
# title clears it whether or not the spinner is currently shown).
_SPINNER_RESERVE = 20


class _SessionRow(QWidget):
    """A single-line, ellipsised title over a muted 'N turns · time' subtitle."""

    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(title)  # full title on hover, since it's elided
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(2)
        self._full_title = title
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self._title = QLabel(title)
        self._title.setFont(_TITLE_FONT)
        self._title.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(False)  # single line — long titles elide (below)
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_row.addWidget(self._title, 1)
        # A small spinner shown while this session has an in-flight (or queued) run.
        self._spinner = QLabel()
        self._spinner.setFixedSize(14, 14)
        self._spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner.setStyleSheet("background: transparent;")
        self._spinner.hide()
        title_row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(title_row)
        sub = QLabel(subtitle)
        sub.setFont(_SUB_FONT)
        sub.setStyleSheet(f"color: {Theme.MUTED}; background: transparent;")
        layout.addWidget(sub)

    def set_running(self, running: bool, *, angle: float = 0.0) -> None:
        if running:
            from dbaide.desktop.components.spinner import spinner_pixmap
            self._spinner.setPixmap(spinner_pixmap(angle, size=13, color=Theme.BLUE))
            self._spinner.show()
        else:
            self._spinner.hide()

    def title(self) -> str:
        return self._full_title

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        avail = max(40, self.width() - 12 - _SPINNER_RESERVE)
        elided = QFontMetrics(_TITLE_FONT).elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, avail
        )
        self._title.setText(elided)

    @staticmethod
    def height_for(title: str, *, content_width: int) -> int:
        """Row height for a single-line title plus the (smaller) subtitle."""
        title_h = QFontMetrics(_TITLE_FONT).lineSpacing()
        sub_h = QFontMetrics(_SUB_FONT).lineSpacing()
        return title_h + sub_h + 18  # margins (7+7) + spacing (2) + a hair


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
        # Session ids with an in-flight/queued run — their rows show a spinner driven
        # by one shared animator (no per-row timers).
        self._running_ids: set[str] = set()
        # Ephemeral rows for brand-new chats that are running but not yet saved
        # (so they appear in the list and can be switched back to mid-run).
        self._pending: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        from dbaide.desktop.components.spinner import BusyAnimator
        self._busy = BusyAnimator(self._tick_spinners)

    def set_running(self, ids: set[str]) -> None:
        """Mark which session ids are currently running (spinner on their rows)."""
        self._running_ids = set(ids or set())
        self._apply_running()
        if self._running_ids and not self._busy.active:
            self._busy.start()
        elif not self._running_ids and self._busy.active:
            self._busy.stop()

    def _apply_running(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = self.list.itemWidget(it)
            if isinstance(w, _SessionRow):
                sid = str(it.data(_ID_ROLE) or "")
                w.set_running(sid in self._running_ids, angle=self._busy.angle)

    def _tick_spinners(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = self.list.itemWidget(it)
            if isinstance(w, _SessionRow):
                sid = str(it.data(_ID_ROLE) or "")
                if sid in self._running_ids:
                    w.set_running(True, angle=self._busy.angle)

    def load(self, sessions: list[dict[str, Any]]) -> None:
        self._sessions = list(sessions or [])
        self._render()

    def set_pending(self, items: list[dict[str, Any]]) -> None:
        """Ephemeral running rows for unsaved new chats — ``[{key, title}]``."""
        if items == self._pending:
            return
        self._pending = list(items or [])
        self._render()

    def _add_row(self, key: str, title: str, subtitle: str) -> None:
        row = _SessionRow(title, subtitle)
        item = QListWidgetItem()
        item.setData(_ID_ROLE, key)
        vw = self.list.viewport().width()
        content_w = (vw if vw > 40 else 232) - 24
        item.setSizeHint(QSize(0, _SessionRow.height_for(title, content_width=content_w)))
        self.list.addItem(item)
        self.list.setItemWidget(item, row)

    def _render(self) -> None:
        self.list.clear()
        # Running unsaved chats first (most relevant, can be switched back to).
        for p in self._pending:
            self._add_row(str(p.get("key") or ""), str(p.get("title") or t("session.new")),
                          t("session.running"))
        if not self._pending and not self._sessions:
            item = QListWidgetItem(t("session.empty"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            from PyQt6.QtGui import QColor
            item.setForeground(QColor(Theme.MUTED))
            self.list.addItem(item)
            self._apply_running()
            return
        from dbaide.history.session_store import DEFAULT_TITLE
        for s in self._sessions:
            sid = str(s.get("session_id") or "")
            title = str(s.get("title") or "")
            if not title or title == DEFAULT_TITLE:
                title = t("session.new")
            n = int(s.get("turn_count") or 0)
            when = _relative_time(float(s.get("updated_at") or s.get("created_at") or 0))
            bits = [t("session.turns_one") if n == 1 else t("session.turns_many", n=n)]
            if when:
                bits.append(when)
            self._add_row(sid, title, " · ".join(bits))
        self._apply_running()  # restore spinners after a rebuild
        self.set_current(self._current)

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
