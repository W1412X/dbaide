"""One dashboard tile: a saved question's chart with refresh / remove controls."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from dbaide.desktop.components.chart_block import build_chart_widget
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t


class DashboardTile(QFrame):
    """Card wrapping one saved question's chart plus a header (refresh / remove)."""

    refresh_requested = pyqtSignal(str)   # question_id
    remove_requested = pyqtSignal(str)    # question_id

    def __init__(self, question: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._question = dict(question or {})
        self._qid = str(self._question.get("id") or "")
        self.setObjectName("dashTile")
        self.setStyleSheet(
            f"QFrame#dashTile {{ background:{Theme.PANEL}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:10px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(14, 10, 12, 12)
        self._root.setSpacing(8)

        # header --------------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(6)
        self._title = QLabel()
        self._title.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)

        self._refresh_btn = IconToolButton(svg_icon("refresh", color=Theme.MUTED, size=14), _t("board.tile_refresh"))
        self._refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self._qid))
        header.addWidget(self._refresh_btn)
        remove_btn = IconToolButton(svg_icon("trash", color=Theme.MUTED, size=14), _t("board.tile_remove"))
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._qid))
        header.addWidget(remove_btn)
        self._root.addLayout(header)

        # chart + footer (rebuilt on every set_question) ----------------------
        self._chart_holder: QWidget | None = None
        self._footer = QLabel()
        self._footer.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent;")
        self._root.addWidget(self._footer)

        self.set_question(self._question)

    # -- public ---------------------------------------------------------------

    def question_id(self) -> str:
        return self._qid

    def question(self) -> dict[str, Any]:
        return dict(self._question)

    def set_loading(self, loading: bool) -> None:
        self._refresh_btn.setEnabled(not loading)
        if loading:
            self._footer.setText(_t("board.tile_refreshing"))

    def set_error(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._footer.setText(_t("board.tile_error", error=str(message)[:120]))
        self._footer.setStyleSheet(f"color:{Theme.RED}; font-size:11px; background:transparent;")

    def set_question(self, question: dict[str, Any]) -> None:
        self._question = dict(question or {})
        self._qid = str(self._question.get("id") or self._qid)
        self._title.setText(str(self._question.get("name") or _t("conversation.chart")))
        # only a query-backed tile (has SQL + plan) can refresh; static snapshots can't
        refreshable = bool(self._question.get("refreshable", False))
        self._refresh_btn.setEnabled(refreshable)
        self._refresh_btn.setVisible(refreshable)
        self._rebuild_chart()
        self._rebuild_footer()

    # -- internals ------------------------------------------------------------

    def _rebuild_chart(self) -> None:
        if self._chart_holder is not None:
            self._root.removeWidget(self._chart_holder)
            self._chart_holder.deleteLater()
            self._chart_holder = None
        spec = self._question.get("chart_spec")
        widget: QWidget | None = None
        if isinstance(spec, dict) and spec.get("chart_type"):
            try:
                widget = build_chart_widget(spec)
            except Exception:
                # WebEngine unavailable, etc. — never let a tile crash the board.
                widget = self._placeholder(str(self._question.get("name") or _t("conversation.chart")))
        if widget is None:
            widget = self._placeholder(_t("board.tile_no_snapshot"))
        self._chart_holder = widget
        self._root.insertWidget(1, widget)   # between header (0) and footer

    def _placeholder(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setMinimumHeight(160)
        label.setStyleSheet(f"color:{Theme.MUTED}; background:transparent; padding:32px;")
        return label

    def _rebuild_footer(self) -> None:
        self._footer.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent;")
        parts: list[str] = []
        if not self._question.get("refreshable", False):
            parts.append(_t("board.tile_static"))
        rows = int(self._question.get("row_count") or 0)
        if rows:
            parts.append(_t("conversation.chart_points", n=rows))
        last = str(self._question.get("last_run_at") or "")
        if last:
            parts.append(_t("board.tile_updated", when=last.replace("T", " ")[:16]))
        self._footer.setText(" · ".join(parts))
