"""Interactive, animated tile grid for a dashboard.

Absolute-positions :class:`DashboardTile`s from the pure packing engine
(``dbaide.boards.grid``). Tiles drag (header) to reorder and resize (corner grip)
to change their grid footprint; both persist via ``layout_changed``.

Motion: while dragging, the other tiles glide out of the way live (the layout is
re-packed with the dragged tile at its hovered index) and the dragged tile lifts.
Only **positions** are animated — sizes change instantly — so the WebEngine
charts inside aren't re-laid-out every frame.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, pyqtSignal
from PyQt6.QtWidgets import QWidget

from dbaide.boards.grid import COLS, clamp_size, grid_rows, move_to_index, pack
from dbaide.desktop.components.dashboard_tile import DashboardTile


class DashboardGrid(QWidget):
    refresh_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)
    layout_changed = pyqtSignal(list)

    ROW_PX = 72
    GAP = 8
    DURATION = 180

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._order: list[dict[str, Any]] = []
        self._questions: dict[str, dict[str, Any]] = {}
        self._tiles: dict[str, DashboardTile] = {}
        self._anims: dict[str, QPropertyAnimation] = {}
        self._resize_base: dict[str, tuple[int, int]] = {}
        self._drag_qid: str | None = None
        self._drag_index: int | None = None
        self._drag_pos: QPoint | None = None

    # -- content --------------------------------------------------------------

    def set_content(self, tiles: list[dict[str, Any]], questions: dict[str, dict[str, Any]]) -> None:
        self._clear()
        for t in tiles:
            qid = str((t or {}).get("question_id") or "")
            q = questions.get(qid)
            if not isinstance(q, dict):
                continue
            w, h = clamp_size(t.get("w"), t.get("h"))
            self._order.append({"question_id": qid, "w": w, "h": h})
            self._questions[qid] = q
            self._build_tile(qid, q)
        self._relayout(animate=False)   # initial placement is instant, not a fly-in

    def tile_ids(self) -> list[str]:
        return [t["question_id"] for t in self._order]

    def tile(self, qid: str) -> DashboardTile | None:
        return self._tiles.get(qid)

    def update_tile(self, qid: str, question: dict[str, Any]) -> None:
        tile = self._tiles.get(qid)
        if tile is not None:
            self._questions[qid] = dict(question)
            tile.set_question(question)

    # -- build / teardown -----------------------------------------------------

    def _build_tile(self, qid: str, question: dict[str, Any]) -> None:
        tile = DashboardTile(question, self)
        tile.refresh_requested.connect(self.refresh_requested)
        tile.remove_requested.connect(self.remove_requested)
        tile.rename_requested.connect(self.rename_requested)
        tile.reorder_drag.connect(self._on_reorder_drag)
        tile.reorder_drop.connect(self._on_reorder_drop)
        tile.resize_drag.connect(self._on_resize_drag)
        tile.resize_drop.connect(self._on_resize_drop)
        tile.show()
        self._tiles[qid] = tile

    def _clear(self) -> None:
        for anim in self._anims.values():
            anim.stop()
        self._anims.clear()
        for tile in self._tiles.values():
            tile.deleteLater()
        self._tiles.clear()
        self._questions.clear()
        self._order = []
        self._resize_base.clear()
        self._drag_qid = self._drag_index = self._drag_pos = None

    # -- geometry -------------------------------------------------------------

    def _col_width(self) -> float:
        return max(1.0, self.width()) / COLS

    def _pixel_rect(self, t: dict[str, Any]) -> QRect:
        cw = self._col_width()
        x = round(t["x"] * cw)
        y = t["y"] * self.ROW_PX
        w = round(t["w"] * cw) - self.GAP
        h = t["h"] * self.ROW_PX - self.GAP
        return QRect(x, y, max(80, w), max(60, h))

    def _positions(self, order: list[dict[str, Any]]) -> dict[str, QRect]:
        return {t["question_id"]: self._pixel_rect(t) for t in pack(order)}

    def _set_instant(self, qid: str, rect: QRect) -> None:
        anim = self._anims.get(qid)
        if anim is not None and anim.state() == QPropertyAnimation.State.Running:
            anim.stop()
        tile = self._tiles.get(qid)
        if tile is not None:
            tile.setGeometry(rect)

    def _animate(self, qid: str, rect: QRect) -> None:
        tile = self._tiles.get(qid)
        if tile is None:
            return
        if tile.size() != rect.size():
            tile.resize(rect.size())       # size changes are instant (cheap; rare in reflow)
        if tile.pos() == rect.topLeft():
            return
        anim = self._anims.get(qid)
        if anim is None:
            anim = QPropertyAnimation(tile, b"pos", self)
            anim.setDuration(self.DURATION)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anims[qid] = anim
        anim.stop()
        anim.setStartValue(tile.pos())
        anim.setEndValue(rect.topLeft())
        anim.start()

    def _relayout(self, *, animate: bool, skip: tuple[str, ...] = ()) -> None:
        place = self._animate if animate else self._set_instant
        for qid, rect in self._positions(self._order).items():
            if qid not in skip:
                place(qid, rect)
        self.setMinimumHeight(grid_rows(pack(self._order)) * self.ROW_PX + 4)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout(animate=False)   # window resize → reflow instantly, no easing lag

    # -- drag reorder (live reflow) -------------------------------------------

    def _on_reorder_drag(self, qid: str, global_pos: QPoint) -> None:
        tile = self._tiles.get(qid)
        if tile is None:
            return
        if self._drag_qid != qid:
            self._drag_qid = qid
            self._drag_index = self._index_of(qid)
            tile.set_dragging(True)
            tile.raise_()
        local = self.mapFromGlobal(global_pos)
        self._drag_pos = local
        # the dragged tile follows the cursor directly (no easing)
        self._set_instant(qid, QRect(local.x() - tile.width() // 2,
                                     max(0, local.y() - 14), tile.width(), tile.height()))
        # re-pack with the dragged tile at its hovered slot and glide the others
        idx = self._reading_index(local, exclude=qid)
        if idx != self._drag_index:
            self._drag_index = idx
            preview = move_to_index(self._order, qid, idx)
            for q, rect in self._positions(preview).items():
                if q != qid:
                    self._animate(q, rect)
            self.setMinimumHeight(grid_rows(pack(preview)) * self.ROW_PX + 4)

    def _on_reorder_drop(self, qid: str) -> None:
        tile = self._tiles.get(qid)
        if tile is not None:
            tile.set_dragging(False)
        if self._drag_index is not None:
            self._order = move_to_index(self._order, qid, self._drag_index)
        self._drag_qid = self._drag_index = self._drag_pos = None
        self._relayout(animate=True)        # ease the dragged tile into its slot too
        self.layout_changed.emit(self._payload())

    def _index_of(self, qid: str) -> int:
        ids = [t["question_id"] for t in self._order if t["question_id"] != qid]
        return min(len(ids), [t["question_id"] for t in self._order].index(qid))

    def _reading_index(self, point: QPoint, exclude: str) -> int:
        """Insertion index among the other tiles, in row-major reading order."""
        cw = self._col_width()
        cursor_rank = (point.y() / self.ROW_PX) * COLS + (point.x() / cw)
        others = [t for t in self._order if t["question_id"] != exclude]
        idx = 0
        for t in pack(others):
            if (t["y"] * COLS + t["x"]) < cursor_rank:
                idx += 1
        return idx

    # -- resize ---------------------------------------------------------------

    def _on_resize_drag(self, qid: str, delta: QPoint) -> None:
        item = next((t for t in self._order if t["question_id"] == qid), None)
        if item is None:
            return
        if qid not in self._resize_base:
            self._resize_base[qid] = (item["w"], item["h"])
        bw, bh = self._resize_base[qid]
        cw = self._col_width()
        new_w, new_h = clamp_size(bw + round(delta.x() / cw), bh + round(delta.y() / self.ROW_PX))
        if (new_w, new_h) != (item["w"], item["h"]):
            item["w"], item["h"] = new_w, new_h
            self._relayout(animate=True, skip=(qid,))   # others glide; active follows the grip
            rects = self._positions(self._order)
            if qid in rects:
                self._set_instant(qid, rects[qid])

    def _on_resize_drop(self, qid: str) -> None:
        self._resize_base.pop(qid, None)
        self._relayout(animate=True)
        self.layout_changed.emit(self._payload())

    def _payload(self) -> list[dict[str, Any]]:
        return [{"question_id": t["question_id"], "w": t["w"], "h": t["h"]} for t in self._order]
