"""Backup configuration dialog and backup manager tab."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.theme import Theme
from dbaide.i18n import t


def _icon_btn(icon_name: str, tooltip: str, *, size: int = 16) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(svg_icon(icon_name, color=Theme.MUTED, size=size, width=1.6))
    btn.setIconSize(QSize(size, size))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedSize(size + 8, size + 8)
    btn.setStyleSheet(
        f"QToolButton {{ background: transparent; border: none; border-radius: 4px;"
        f" padding: 0; margin: 0; min-width: 0; min-height: 0; }}"
        f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        f"QToolButton:pressed {{ background: {Theme.PANEL_3}; }}"
        f"QToolButton:disabled {{ opacity: 0.3; }}"
    )
    return btn


# ── Backup worker thread ─────────────────────────────────────────────────────


class _BackupWorker(QThread):
    progress = pyqtSignal(str, int, object)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config, database: str, table: str, scope: str,
                 fmt: str, batch_size: int, threads: int) -> None:
        super().__init__()
        self._config = config
        self._database = database
        self._table = table
        self._scope = scope
        self._fmt = fmt
        self._batch_size = batch_size
        self._threads = threads

    def run(self) -> None:
        try:
            from dbaide.backup import BackupEngine
            engine = BackupEngine(self._config)

            def on_progress(table, done, total):
                self.progress.emit(table, done, total)

            if self._scope == "table":
                result = engine.backup_table(
                    self._database, self._table,
                    fmt=self._fmt, batch_size=self._batch_size,
                    on_progress=on_progress,
                )
                self.finished.emit([result])
            else:
                results = engine.backup_database(
                    self._database,
                    fmt=self._fmt, batch_size=self._batch_size,
                    threads=self._threads,
                    on_progress=on_progress,
                )
                self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Backup Dialog ─────────────────────────────────────────────────────────────


_INPUT_STYLE = (
    f"background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
    f" border-radius: 6px; padding: 3px 8px; color: {Theme.TEXT}; font-size: 12px;"
)


class BackupDialog(QDialog):
    def __init__(self, config, database: str, table: str = "",
                 scope: str = "table", parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._database = database
        self._table = table
        self._scope = scope
        self._worker: _BackupWorker | None = None

        self.setWindowTitle(t("backup.title"))
        self.setFixedWidth(320)
        self.setStyleSheet(
            f"QDialog {{ background: {Theme.SURFACE}; color: {Theme.TEXT}; }}"
            f"QLabel {{ background: transparent; }}"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        # Target label — small, muted
        if scope == "table":
            target = f"{database}.{table}" if database else table
        else:
            target = database
        target_label = QLabel(target)
        target_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {Theme.TEXT};"
            f" padding-bottom: 2px;"
        )
        layout.addWidget(target_label)

        # Inline form: Format | Batch | Threads in a compact grid
        form = QWidget()
        form.setStyleSheet("background: transparent;")
        form_layout = QHBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        # Format
        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(2)
        fl = QLabel(t("backup.format"))
        fl.setStyleSheet(f"font-size: 10px; color: {Theme.MUTED};")
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["csv", "sql", "sqlite"])
        self._fmt_combo.setFixedHeight(26)
        self._fmt_combo.setStyleSheet(f"QComboBox {{ {_INPUT_STYLE} }}")
        fmt_col.addWidget(fl)
        fmt_col.addWidget(self._fmt_combo)
        form_layout.addLayout(fmt_col, 1)

        # Batch size
        batch_col = QVBoxLayout()
        batch_col.setSpacing(2)
        bl = QLabel(t("backup.batch_size"))
        bl.setStyleSheet(f"font-size: 10px; color: {Theme.MUTED};")
        self._batch_spin = QSpinBox()
        self._batch_spin.setRange(100, 100_000)
        self._batch_spin.setValue(5000)
        self._batch_spin.setSingleStep(1000)
        self._batch_spin.setFixedHeight(26)
        self._batch_spin.setStyleSheet(f"QSpinBox {{ {_INPUT_STYLE} }}")
        batch_col.addWidget(bl)
        batch_col.addWidget(self._batch_spin)
        form_layout.addLayout(batch_col, 1)

        # Threads (database scope only)
        if scope == "database":
            th_col = QVBoxLayout()
            th_col.setSpacing(2)
            tl = QLabel(t("backup.threads"))
            tl.setStyleSheet(f"font-size: 10px; color: {Theme.MUTED};")
            self._thread_spin = QSpinBox()
            self._thread_spin.setRange(1, 16)
            self._thread_spin.setValue(4)
            self._thread_spin.setFixedHeight(26)
            self._thread_spin.setStyleSheet(f"QSpinBox {{ {_INPUT_STYLE} }}")
            th_col.addWidget(tl)
            th_col.addWidget(self._thread_spin)
            form_layout.addLayout(th_col, 1)
        else:
            self._thread_spin = None

        layout.addWidget(form)

        # Bottom row: status + start button
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: 11px; color: {Theme.MUTED}; background: transparent;")
        self._status.setWordWrap(True)
        bottom.addWidget(self._status, 1)

        self._start_btn = QPushButton(t("backup.start"))
        self._start_btn.setFixedHeight(28)
        self._start_btn.setFixedWidth(90)
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background: {Theme.ACCENT}; color: white;"
            f" border: none; border-radius: 6px; padding: 0 12px;"
            f" font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {Theme.ACCENT_HOVER}; }}"
            f"QPushButton:disabled {{ background: {Theme.PANEL_2}; color: {Theme.MUTED}; }}"
        )
        self._start_btn.clicked.connect(self._start_backup)
        bottom.addWidget(self._start_btn)
        layout.addLayout(bottom)

    def _start_backup(self) -> None:
        self._start_btn.setEnabled(False)
        fmt = self._fmt_combo.currentText()
        batch_size = self._batch_spin.value()
        threads = self._thread_spin.value() if self._thread_spin else 1
        self._status.setText(t("backup.running").format(
            target=f"{self._database}.{self._table}" if self._table else self._database,
        ))
        self._worker = _BackupWorker(
            self._config, self._database, self._table, self._scope,
            fmt, batch_size, threads,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, table: str, done: int, total: object) -> None:
        if total:
            pct = min(int(done / int(total) * 100), 100)
            self._status.setText(f"{table}: {done:,}/{int(total):,} ({pct}%)")
        else:
            self._status.setText(f"{table}: {done:,} rows")

    def _on_finished(self, results: list) -> None:
        ok = [r for r in results if not r.get("error")]
        errors = [r for r in results if r.get("error")]
        total_rows = sum(r.get("row_count", 0) for r in ok)
        msg = t("backup.done").format(count=len(ok), rows=f"{total_rows:,}")
        if errors:
            msg += f"\n{len(errors)} failed"
        self._status.setText(msg)
        self._start_btn.setEnabled(True)

    def _on_error(self, error: str) -> None:
        self._status.setText(t("backup.failed").format(error=error))
        self._start_btn.setEnabled(True)


# ── Backup Manager (Workbench Tab) ───────────────────────────────────────────


class BackupManager(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header row: title + icon buttons
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel(t("backup.manager"))
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {Theme.TEXT};")
        header.addWidget(title)
        header.addStretch()

        self._refresh_btn = _icon_btn("refresh", t("data.refresh"))
        self._refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self._refresh_btn)

        self._open_btn = _icon_btn("folder-open", t("backup.open_folder"))
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_folder)
        header.addWidget(self._open_btn)

        self._delete_btn = _icon_btn("trash", t("backup.delete"))
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        header.addWidget(self._delete_btn)

        layout.addLayout(header)

        # Table
        col_headers = [
            t("backup.col.table"),
            t("backup.col.database"),
            t("backup.col.date"),
            t("backup.col.rows"),
            t("backup.col.size"),
            t("backup.col.format"),
        ]
        self._grid = QTableWidget(0, len(col_headers))
        self._grid.setHorizontalHeaderLabels(col_headers)
        self._grid.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._grid.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._grid.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._grid.verticalHeader().setVisible(False)
        self._grid.setShowGrid(False)
        self._grid.setAlternatingRowColors(True)
        self._grid.verticalHeader().setDefaultSectionSize(30)

        hdr = self._grid.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)      # Table
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)      # Database
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)      # Date
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Rows
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Size
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Format

        self._grid.setStyleSheet(
            f"QTableWidget {{ background: {Theme.SURFACE}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 8px; }}"
            f"QTableWidget::item {{ padding: 2px 8px; border: none; }}"
            f"QTableWidget::item:selected {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}"
            f"QTableWidget::item:alternate {{ background: {Theme.PANEL}; }}"
            f"QHeaderView::section {{ background: {Theme.SURFACE}; color: {Theme.MUTED};"
            f" border: none; border-bottom: 1px solid {Theme.BORDER}; padding: 4px 8px;"
            f" font-size: 11px; font-weight: 500; }}"
        )
        self._grid.selectionModel().selectionChanged.connect(self._on_selection)
        layout.addWidget(self._grid)

        # Empty state
        self._empty = QLabel(t("backup.empty"))
        self._empty.setStyleSheet(f"color: {Theme.MUTED}; padding: 32px; font-size: 12px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty)

        self._records: list[Any] = []
        self.refresh()

    def refresh(self) -> None:
        from dbaide.backup import BackupRegistry

        prev_row = -1
        indexes = self._grid.selectionModel().selectedRows()
        if indexes:
            prev_row = indexes[0].row()

        registry = BackupRegistry()
        self._records = registry.list_backups()
        self._grid.setRowCount(len(self._records))
        for row, rec in enumerate(self._records):
            self._grid.setItem(row, 0, QTableWidgetItem(rec.table))
            self._grid.setItem(row, 1, QTableWidgetItem(rec.database))
            self._grid.setItem(row, 2, QTableWidgetItem(rec.timestamp))

            rows_item = QTableWidgetItem(f"{rec.row_count:,}")
            rows_item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self._grid.setItem(row, 3, rows_item)

            size_item = QTableWidgetItem(_fmt_size(rec.file_size))
            size_item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self._grid.setItem(row, 4, size_item)

            fmt_item = QTableWidgetItem(rec.format.upper())
            fmt_item.setForeground(QColor(Theme.MUTED))
            fmt_item.setFont(QFont("", -1, -1))
            self._grid.setItem(row, 5, fmt_item)

        has = len(self._records) > 0
        self._grid.setVisible(has)
        self._empty.setVisible(not has)
        if has and 0 <= prev_row < len(self._records):
            self._grid.selectRow(prev_row)
        else:
            self._delete_btn.setEnabled(False)
            self._open_btn.setEnabled(False)

    def _on_selection(self) -> None:
        has = bool(self._grid.selectionModel().hasSelection())
        self._delete_btn.setEnabled(has)
        self._open_btn.setEnabled(has)

    def _selected_record(self) -> Any | None:
        indexes = self._grid.selectionModel().selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        return self._records[row] if row < len(self._records) else None

    def _delete_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        reply = QMessageBox.question(self, t("backup.delete"), t("backup.delete_confirm"))
        if reply != QMessageBox.StandardButton.Yes:
            return
        from dbaide.backup import BackupRegistry
        registry = BackupRegistry()
        registry.delete(rec.id)
        self.refresh()

    def _open_folder(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        folder = str(Path(rec.file_path).parent)
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", folder])


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
