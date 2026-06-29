"""Live view of the SQL cost governor's execution pool.

A compact, clickable status-bar indicator shows how many queries are running vs.
queued and how much of the shared cost budget is in use; clicking it opens a dialog
that lists each running and queued query with its EXPLAIN cost, connection, and how
long it has been running / waiting. Both poll :data:`governor` on a timer.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
)

from dbaide.core.sql_governor import governor
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t


def _fmt_secs(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"


class SqlPoolIndicator(QToolButton):
    """Status-bar widget: running/queued counts + budget use. Hidden when the cost
    governor is disabled; clicking opens the pool dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_t("sqlpool.title"))
        self.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; color: {Theme.TEXT_2};"
            f" font-size: 11px; padding: 0 8px; }}"
            f"QToolButton:hover {{ color: {Theme.TEXT}; }}")
        self._dialog: SqlPoolDialog | None = None
        self.clicked.connect(self._open)
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        snap = governor.snapshot()
        if not snap["enabled"]:
            self.setVisible(False)
            return
        self.setVisible(True)
        budget = snap["budget"] or 1
        pct = int(round(100 * snap["in_flight_cost"] / budget))
        self.setText(_t("sqlpool.indicator", running=snap["running_count"],
                        queued=snap["queued_count"], pct=pct))

    def _open(self) -> None:
        if self._dialog is None:
            self._dialog = SqlPoolDialog(self.window())
            self._dialog.finished.connect(lambda *_: setattr(self, "_dialog", None))
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()


class SqlPoolDialog(QDialog):
    """Lists the running and queued queries with cost, connection, and timing."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("sqlpool.title"))
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        self._budget_lbl = QLabel()
        self._budget_lbl.setStyleSheet(f"color:{Theme.TEXT}; font-size:13px; font-weight:600;")
        lay.addWidget(self._budget_lbl)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background:{Theme.PANEL_2}; border:none; border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{Theme.ACCENT}; border-radius:4px; }}")
        lay.addWidget(self._bar)

        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color:{Theme.MUTED}; font-size:13px; padding:24px;")
        lay.addWidget(self._empty)

        lay.addWidget(self._heading(_t("sqlpool.running")))
        self._running = self._make_table(_t("sqlpool.col_elapsed"))
        lay.addWidget(self._running, 1)
        lay.addWidget(self._heading(_t("sqlpool.queued")))
        self._queued = self._make_table(_t("sqlpool.col_waited"))
        lay.addWidget(self._queued, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # -- construction helpers -------------------------------------------------

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:12px; font-weight:600; padding-top:4px;")
        return lbl

    def _make_table(self, last_col: str) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(
            [_t("sqlpool.col_sql"), _t("sqlpool.col_cost"), _t("sqlpool.col_conn"), last_col])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        return table

    # -- live refresh ---------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        snap = governor.snapshot()
        if not snap["enabled"]:
            self._budget_lbl.setText(_t("sqlpool.disabled"))
            self._bar.setVisible(False)
            self._empty.setVisible(True)
            self._empty.setText(_t("sqlpool.disabled"))
            self._running.setRowCount(0)
            self._queued.setRowCount(0)
            return
        self._bar.setVisible(True)
        budget = snap["budget"] or 1
        used = snap["in_flight_cost"]
        self._budget_lbl.setText(_t("sqlpool.budget", used=f"{used:,}", budget=f"{snap['budget']:,}",
                                    pct=int(round(100 * used / budget))))
        self._bar.setRange(0, snap["budget"])
        self._bar.setValue(min(used, snap["budget"]))
        self._fill(self._running, snap["running"], "elapsed_s")
        self._fill(self._queued, snap["queued"], "waited_s")
        idle = not snap["running"] and not snap["queued"]
        self._empty.setVisible(idle)
        self._empty.setText(_t("sqlpool.empty") if idle else "")

    def _fill(self, table: QTableWidget, rows: list[dict], time_key: str) -> None:
        table.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(entry["label"]))
            cost = QTableWidgetItem(f"{entry['cost']:,}")
            cost.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 1, cost)
            table.setItem(r, 2, QTableWidgetItem(entry.get("connection") or "—"))
            table.setItem(r, 3, QTableWidgetItem(_fmt_secs(entry.get(time_key, 0.0))))
