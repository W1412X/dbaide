"""Interactive tile grid for a dashboard.

Absolute-positions :class:`DashboardTile`s from the pure packing engine
(``dbaide.boards.grid``). Tiles can be dragged by their header to reorder and
resized by a corner grip to change their grid footprint; both emit
``layout_changed`` with the new order + sizes for the tab to persist. All
geometry math is in the (unit-tested) engine — this widget just maps grid units
to pixels and turns mouse gestures into ``move_to_index`` / size changes.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, QRect, pyqtSignal
from PyQt6.QtWidgets import QWidget

from dbaide.boards.grid import COLS, clamp_size, grid_rows, move_to_index, pack
from dbaide.desktop.components.dashboard_tile import DashboardTile


class DashboardGrid(QWidget):
    refresh_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)
    layout_changed = pyqtSignal(list)        # [{question_id, w, h}, …] in order

    ROW_PX = 72
    GAP = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._order: list[dict[str, Any]] = []      # ordered tile dicts: {question_id, w, h}
        self._questions: dict[str, dict[str, Any]] = {}
        self._tiles: dict[str, DashboardTile] = {}
        self._resize_base: dict[str, tuple[int, int]] = {}
        self._drag_pos: QPoint | None = None

    # -- content --------------------------------------------------------------

    def set_content(self, tiles: list[dict[str, Any]], questions: dict[str, dict[str, Any]]) -> None:
        """``tiles`` are board tiles ({question_id, w, h, …}) in order; ``questions``
        maps id → saved-question dict. Dangling tile refs are skipped."""
        self._clear()
        self._order = []
        for t in tiles:
            qid = str((t or {}).get("question_id") or "")
            q = questions.get(qid)
            if not isinstance(q, dict):
                continue
            w, h = clamp_size(t.get("w"), t.get("h"))
            self._order.append({"question_id": qid, "w": w, "h": h})
            self._questions[qid] = q
            self._build_tile(qid, q)
        self._relayout()

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
        for tile in self._tiles.values():
            tile.deleteLater()
        self._tiles.clear()
        self._questions.clear()
        self._order = []
        self._resize_base.clear()

    # -- layout ---------------------------------------------------------------

    def _col_width(self) -> float:
        return max(1.0, self.width()) / COLS

    def _pixel_rect(self, t: dict[str, Any]) -> QRect:
        cw = self._col_width()
        x = round(t["x"] * cw)
        y = t["y"] * self.ROW_PX
        w = round(t["w"] * cw) - self.GAP
        h = t["h"] * self.ROW_PX - self.GAP
        return QRect(x, y, max(80, w), max(60, h))

    def _relayout(self) -> None:
        packed = pack(self._order)
        for t in packed:
            tile = self._tiles.get(t["question_id"])
            if tile is not None:
                tile.setGeometry(self._pixel_rect(t))
        self.setMinimumHeight(grid_rows(packed) * self.ROW_PX + 4)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    # -- drag reorder ---------------------------------------------------------

    def _on_reorder_drag(self, qid: str, global_pos: QPoint) -> None:
        tile = self._tiles.get(qid)
        if tile is None:
            return
        tile.raise_()
        local = self.mapFromGlobal(global_pos)
        self._drag_pos = local
        # follow the cursor so the move is visible; relayout on drop snaps it
        tile.move(local.x() - tile.width() // 2, max(0, local.y() - 14))

    def _on_reorder_drop(self, qid: str) -> None:
        if self._drag_pos is not None:
            idx = self._drop_index(self._drag_pos, qid)
            self._order = move_to_index(self._order, qid, idx)
        self._drag_pos = None
        self._relayout()
        self.layout_changed.emit(self._payload())

    def _drop_index(self, point: QPoint, dragged: str) -> int:
        """Insertion index from a drop point, by which tile it landed on."""
        order_ids = [t["question_id"] for t in self._order if t["question_id"] != dragged]
        packed = {t["question_id"]: t for t in pack(self._order)}
        for i, qid in enumerate(order_ids):
            rect = self._pixel_rect(packed[qid])
            if rect.contains(point):
                return i if point.x() < rect.center().x() else i + 1
        # below everything → append; above/left → front
        if not order_ids:
            return 0
        return len(order_ids)

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
            self._relayout()

    def _on_resize_drop(self, qid: str) -> None:
        self._resize_base.pop(qid, None)
        self.layout_changed.emit(self._payload())

    def _payload(self) -> list[dict[str, Any]]:
        return [{"question_id": t["question_id"], "w": t["w"], "h": t["h"]} for t in self._order]
