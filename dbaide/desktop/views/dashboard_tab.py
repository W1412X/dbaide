"""Dashboard view: pick a board, see its tiles, refresh them on demand.

Tiles reference saved questions by id. Refreshing re-runs the saved SQL and
rebuilds the chart deterministically — that work happens on a background thread
(``_RefreshWorker``) so the UI never blocks on a query.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.dashboard_tile import DashboardTile
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t

_GRID_COLS = 2


class _RefreshWorker(QThread):
    """Re-run a list of saved questions sequentially, off the UI thread."""

    tile_done = pyqtSignal(str, object)   # question_id, result dict
    tile_failed = pyqtSignal(str, str)    # question_id, error
    finished_all = pyqtSignal()

    def __init__(self, service, question_ids: list[str], parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._ids = list(question_ids)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for qid in self._ids:
            if self._cancelled:
                break
            try:
                res = self._service.dispatch("refresh_saved_question", {"id": qid})
                if not self._cancelled:
                    self.tile_done.emit(qid, res)
            except BaseException as exc:  # noqa: BLE001 — report, never crash the thread
                if not self._cancelled:
                    self.tile_failed.emit(qid, str(exc))
        self.finished_all.emit()


class DashboardTab(QWidget):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._board_id = ""
        self._tiles: dict[str, DashboardTile] = {}
        self._worker: _RefreshWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # toolbar -------------------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._picker = QComboBox()
        self._picker.setMinimumWidth(200)
        self._picker.currentIndexChanged.connect(self._on_pick)
        bar.addWidget(self._picker)
        new_btn = IconToolButton(svg_icon("plus", color=Theme.MUTED, size=15), _t("board.new"))
        new_btn.clicked.connect(self._on_new_board)
        bar.addWidget(new_btn)
        self._rename_btn = IconToolButton(svg_icon("pencil", color=Theme.MUTED, size=14), _t("board.rename"))
        self._rename_btn.clicked.connect(self._on_rename_board)
        bar.addWidget(self._rename_btn)
        self._delete_btn = IconToolButton(svg_icon("trash", color=Theme.MUTED, size=14), _t("board.delete"))
        self._delete_btn.clicked.connect(self._on_delete_board)
        bar.addWidget(self._delete_btn)
        bar.addStretch(1)
        self._refresh_all = compact_button(_t("board.refresh_all"), width=110)
        self._refresh_all.clicked.connect(self._on_refresh_all)
        bar.addWidget(self._refresh_all)
        root.addLayout(bar)

        # tile grid -----------------------------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)

        self._empty = QLabel(_t("board.empty"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color:{Theme.MUTED}; font-size:13px; background:transparent;")
        root.addWidget(self._empty)

        self.reload()

    # -- data loading ---------------------------------------------------------

    def shutdown(self) -> None:
        """Stop any in-flight refresh (call before the window/tab is destroyed)."""
        self._stop_worker()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None              # claim it first so _on_refresh_finished no-ops
        worker.cancel()
        worker.wait()                    # bounded by the in-flight query (read-only, short)
        worker.deleteLater()
        self._refresh_all.setEnabled(True)

    def reload(self) -> None:
        """Refresh the board list (call when shown or after a pin elsewhere)."""
        self._stop_worker()              # never rebuild tiles out from under a live worker
        try:
            boards = self._service.dispatch("list_dashboards", {}).get("dashboards", [])
        except Exception:
            boards = []
        self._picker.blockSignals(True)
        self._picker.clear()
        for board in boards:
            self._picker.addItem(str(board.get("name") or ""), str(board.get("id") or ""))
        # keep current selection if still present, else first
        idx = max(0, self._picker.findData(self._board_id))
        self._picker.setCurrentIndex(idx)
        self._board_id = str(self._picker.currentData() or "")
        self._picker.blockSignals(False)
        has_board = bool(boards)
        self._picker.setVisible(has_board)
        self._rename_btn.setVisible(has_board)
        self._delete_btn.setVisible(has_board)
        self._refresh_all.setVisible(has_board)
        if has_board:
            self._load_board(self._board_id)
        else:
            self._clear_grid()
            self._show_empty(_t("board.empty"))

    def _load_board(self, board_id: str) -> None:
        self._stop_worker()   # _on_pick / tile-remove also funnel here
        self._clear_grid()
        if not board_id:
            self._show_empty(_t("board.empty"))
            return
        try:
            data = self._service.dispatch("get_dashboard", {"id": board_id})
        except Exception:
            self._show_empty(_t("board.empty"))
            return
        board = data.get("dashboard") or {}
        questions = data.get("questions") or {}
        tiles = board.get("tiles") or []
        if not tiles:
            self._show_empty(_t("board.empty_board"))
            return
        self._empty.setVisible(False)
        for i, tile in enumerate(tiles):
            qid = str((tile or {}).get("question_id") or "")
            question = questions.get(qid)
            if not isinstance(question, dict):
                continue   # dangling reference (question deleted) — skip
            widget = DashboardTile(question)
            widget.refresh_requested.connect(self._on_tile_refresh)
            widget.remove_requested.connect(self._on_tile_remove)
            self._tiles[qid] = widget
            self._grid.addWidget(widget, i // _GRID_COLS, i % _GRID_COLS)
        if not self._tiles:
            self._show_empty(_t("board.empty_board"))

    # -- board actions --------------------------------------------------------

    def _on_pick(self, _index: int) -> None:
        self._board_id = str(self._picker.currentData() or "")
        self._load_board(self._board_id)

    def _on_new_board(self) -> None:
        name, ok = QInputDialog.getText(self, _t("board.new"), _t("board.new_prompt"))
        if not ok or not name.strip():
            return
        board = self._service.dispatch("create_dashboard", {"name": name.strip()}).get("dashboard") or {}
        self._board_id = str(board.get("id") or "")
        self.reload()

    def _on_rename_board(self) -> None:
        if not self._board_id:
            return
        current = self._picker.currentText()
        name, ok = QInputDialog.getText(self, _t("board.rename"), _t("board.rename_prompt"), text=current)
        if not ok or not name.strip():
            return
        self._service.dispatch("rename_dashboard", {"id": self._board_id, "name": name.strip()})
        self.reload()

    def _on_delete_board(self) -> None:
        if not self._board_id:
            return
        if QMessageBox.question(self, _t("board.delete"), _t("board.delete_confirm")) != QMessageBox.StandardButton.Yes:
            return
        self._service.dispatch("delete_dashboard", {"id": self._board_id})
        self._board_id = ""
        self.reload()

    # -- tile actions ---------------------------------------------------------

    def _on_tile_remove(self, qid: str) -> None:
        # Remove the tile from this board only — the saved question stays in the
        # library (it may live on other boards). Deleting the question is separate.
        self._service.dispatch("remove_tile", {"dashboard_id": self._board_id, "question_id": qid})
        self._load_board(self._board_id)

    def _on_tile_refresh(self, qid: str) -> None:
        self._start_refresh([qid])

    def _on_refresh_all(self) -> None:
        # only query-backed tiles can refresh; skip static snapshots
        self._start_refresh([qid for qid, t in self._tiles.items() if t.question().get("refreshable")])

    def _start_refresh(self, qids: list[str]) -> None:
        if self._worker is not None or not qids:
            return
        for qid in qids:
            tile = self._tiles.get(qid)
            if tile is not None:
                tile.set_loading(True)
        self._refresh_all.setEnabled(False)
        worker = _RefreshWorker(self._service, qids, self)
        worker.tile_done.connect(self._on_tile_done)
        worker.tile_failed.connect(self._on_tile_failed)
        worker.finished_all.connect(self._on_refresh_finished)
        self._worker = worker
        worker.start()

    def _on_tile_done(self, qid: str, result: object) -> None:
        tile = self._tiles.get(qid)
        if tile is None or not isinstance(result, dict):
            return
        from dbaide.boards.models import utc_now
        # merge fresh chart into the tile's question view
        question = {**tile.question(), "id": qid}
        question["chart_spec"] = result.get("chart_spec")
        question["row_count"] = result.get("row_count", question.get("row_count"))
        question["refreshable"] = result.get("refreshable", question.get("refreshable"))
        question["last_run_at"] = utc_now()
        tile.set_question(question)

    def _on_tile_failed(self, qid: str, error: str) -> None:
        tile = self._tiles.get(qid)
        if tile is not None:
            tile.set_error(error)

    def _on_refresh_finished(self) -> None:
        worker = self._worker
        if worker is None:
            return   # already force-stopped by _stop_worker (reload/close)
        self._worker = None
        self._refresh_all.setEnabled(True)
        worker.wait()
        worker.deleteLater()

    # -- helpers --------------------------------------------------------------

    def _clear_grid(self) -> None:
        for tile in self._tiles.values():
            self._grid.removeWidget(tile)
            tile.deleteLater()
        self._tiles.clear()

    def _show_empty(self, text: str) -> None:
        self._empty.setText(text)
        self._empty.setVisible(True)
