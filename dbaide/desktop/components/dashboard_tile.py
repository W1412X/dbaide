"""One dashboard tile: a saved question's chart, with an editable title, a drag
handle (reorder), a resize grip (footprint), and refresh / remove controls.

Visually deliberately *un*-card-like — minimal chrome, tight padding — so a board
reads as one surface rather than a tray of boxes. The geometry math lives in the
pure ``dbaide.boards.grid`` engine; this widget only emits intent signals.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.chart_block import build_chart_widget
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t

_DRAG_THRESHOLD = 6


class _DragHeader(QWidget):
    """Header strip that starts a reorder drag when pressed-and-moved."""

    drag_move = pyqtSignal(QPoint)   # global cursor position while dragging
    drag_drop = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._press: QPoint | None = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None:
            return
        gp = e.globalPosition().toPoint()
        if not self._dragging and (gp - self._press).manhattanLength() >= _DRAG_THRESHOLD:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.drag_move.emit(gp)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_drop.emit()
        self._press = None
        super().mouseReleaseEvent(e)


class _ResizeGrip(QLabel):
    """Bottom-right corner grip that resizes the tile's grid footprint."""

    resize_move = pyqtSignal(QPoint)   # cumulative global delta from press
    resize_drop = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__("⤡", parent)
        self._press: QPoint | None = None
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet(f"color:{Theme.MUTED}; background:transparent; font-size:12px;")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is not None:
            self.resize_move.emit(e.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._press is not None:
            self._press = None
            self.resize_drop.emit()
            e.accept()


class _EditableTitle(QLabel):
    """Title label; double-click to rename, single press falls through to drag."""

    edit_requested = pyqtSignal()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.edit_requested.emit()
        e.accept()


class DashboardTile(QFrame):
    refresh_requested = pyqtSignal(str)             # question_id
    remove_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)         # question_id, new name
    reorder_drag = pyqtSignal(str, QPoint)          # question_id, global pos
    reorder_drop = pyqtSignal(str)
    resize_drag = pyqtSignal(str, QPoint)           # question_id, cumulative delta
    resize_drop = pyqtSignal(str)

    def __init__(self, question: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._question = dict(question or {})
        self._qid = str(self._question.get("id") or "")
        self._editing = False
        self.setObjectName("dashTile")
        # Transparent by default so the board reads as one surface (the chart already
        # shares the board background); only a hovered tile gets a faint panel to show
        # it's the active target for drag/resize/controls.
        self.setStyleSheet(
            f"QFrame#dashTile {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QFrame#dashTile:hover {{ background: {Theme.PANEL}; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 4, 8, 8)
        self._root.setSpacing(4)

        # header: drag handle wrapping [title | refresh | remove] ---------------
        self._header = _DragHeader()
        self._header.drag_move.connect(lambda gp: self.reorder_drag.emit(self._qid, gp))
        self._header.drag_drop.connect(lambda: self.reorder_drop.emit(self._qid))
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._title = _EditableTitle()
        self._title.setFont(QFont("Inter", 12, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
        self._title.setToolTip(_t("board.tile_rename_hint"))
        self._title.edit_requested.connect(self._begin_rename)
        hl.addWidget(self._title, 1)
        self._editor = QLineEdit()
        self._editor.setVisible(False)
        self._editor.setStyleSheet(
            f"QLineEdit {{ background:{Theme.PANEL_2}; border:1px solid {Theme.ACCENT};"
            f" border-radius:4px; color:{Theme.TEXT}; padding:1px 4px; }}")
        self._editor.editingFinished.connect(self._commit_rename)
        hl.addWidget(self._editor, 1)
        self._editor.hide()
        self._refresh_btn = IconToolButton(svg_icon("refresh", color=Theme.MUTED, size=13), _t("board.tile_refresh"))
        self._refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self._qid))
        hl.addWidget(self._refresh_btn)
        remove_btn = IconToolButton(svg_icon("trash", color=Theme.MUTED, size=13), _t("board.tile_remove"))
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._qid))
        hl.addWidget(remove_btn)
        self._root.addWidget(self._header)

        # chart (rebuilt on set_question) + footer ------------------------------
        self._chart_holder: QWidget | None = None
        self._footer = QLabel()
        self._footer.setStyleSheet(f"color:{Theme.MUTED}; font-size:10px; background:transparent;")
        self._root.addWidget(self._footer)

        # resize grip pinned to the bottom-right corner -------------------------
        self._grip = _ResizeGrip(self)
        self._grip.resize_move.connect(lambda d: self.resize_drag.emit(self._qid, d))
        self._grip.resize_drop.connect(lambda: self.resize_drop.emit(self._qid))

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
        self._footer.setStyleSheet(f"color:{Theme.RED}; font-size:10px; background:transparent;")

    def set_question(self, question: dict[str, Any]) -> None:
        self._question = dict(question or {})
        self._qid = str(self._question.get("id") or self._qid)
        self._title.setText(str(self._question.get("name") or _t("conversation.chart")))
        refreshable = bool(self._question.get("refreshable", False))
        self._refresh_btn.setEnabled(refreshable)
        self._refresh_btn.setVisible(refreshable)
        self._rebuild_chart()
        self._rebuild_footer()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._grip.move(self.width() - self._grip.width() - 2, self.height() - self._grip.height() - 2)

    # -- rename ---------------------------------------------------------------

    def _begin_rename(self) -> None:
        self._editing = True
        self._editor.setText(str(self._question.get("name") or ""))
        self._title.setVisible(False)
        self._editor.setVisible(True)
        self._editor.selectAll()
        self._editor.setFocus()

    def _commit_rename(self) -> None:
        if not self._editing:
            return
        self._editing = False
        name = self._editor.text().strip()
        self._editor.setVisible(False)
        self._title.setVisible(True)
        if name and name != str(self._question.get("name") or ""):
            self._question["name"] = name
            self._title.setText(name)
            self.rename_requested.emit(self._qid, name)

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
                widget = self._placeholder(str(self._question.get("name") or _t("conversation.chart")))
        if widget is None:
            widget = self._placeholder(_t("board.tile_no_snapshot"))
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._chart_holder = widget
        self._root.insertWidget(1, widget, 1)   # between header (0) and footer

    def _rebuild_footer(self) -> None:
        self._footer.setStyleSheet(f"color:{Theme.MUTED}; font-size:10px; background:transparent;")
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

    def _placeholder(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{Theme.MUTED}; background:transparent; padding:20px;")
        return label
