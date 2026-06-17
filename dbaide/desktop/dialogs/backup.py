"""Backup configuration dialog and backup manager tab."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, Qt, pyqtSignal
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
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.theme import Theme
from dbaide.i18n import t


# ── Backup worker thread ─────────────────────────────────────────────────────


class _BackupWorker(QThread):
    progress = pyqtSignal(str, int, object)  # table, done_rows, total_estimate
    finished = pyqtSignal(list)  # list of result dicts
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
        self.setMinimumWidth(360)
        self.setStyleSheet(f"background: {Theme.SURFACE}; color: {Theme.TEXT};")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # Target label
        if scope == "table":
            target = f"{database}.{table}" if database else table
        else:
            target = database
        title = QLabel(f"{t('backup.title')}: {target}")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {Theme.TEXT};")
        layout.addWidget(title)

        # Format selector
        fmt_row = QHBoxLayout()
        fmt_label = QLabel(t("backup.format"))
        fmt_label.setFixedWidth(90)
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["csv", "sql", "sqlite"])
        self._fmt_combo.setFixedWidth(120)
        self._fmt_combo.setStyleSheet(
            f"QComboBox {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 6px; padding: 4px 8px; }}"
        )
        fmt_row.addWidget(fmt_label)
        fmt_row.addWidget(self._fmt_combo)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        # Batch size
        batch_row = QHBoxLayout()
        batch_label = QLabel(t("backup.batch_size"))
        batch_label.setFixedWidth(90)
        self._batch_spin = QSpinBox()
        self._batch_spin.setRange(100, 100_000)
        self._batch_spin.setValue(5000)
        self._batch_spin.setSingleStep(1000)
        self._batch_spin.setFixedWidth(120)
        self._batch_spin.setStyleSheet(
            f"QSpinBox {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 6px; padding: 4px 8px; }}"
        )
        batch_row.addWidget(batch_label)
        batch_row.addWidget(self._batch_spin)
        batch_row.addStretch()
        layout.addLayout(batch_row)

        # Threads (only for database scope)
        if scope == "database":
            thread_row = QHBoxLayout()
            thread_label = QLabel(t("backup.threads"))
            thread_label.setFixedWidth(90)
            self._thread_spin = QSpinBox()
            self._thread_spin.setRange(1, 16)
            self._thread_spin.setValue(4)
            self._thread_spin.setFixedWidth(120)
            self._thread_spin.setStyleSheet(
                f"QSpinBox {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
                f" border-radius: 6px; padding: 4px 8px; }}"
            )
            thread_row.addWidget(thread_label)
            thread_row.addWidget(self._thread_spin)
            thread_row.addStretch()
            layout.addLayout(thread_row)
        else:
            self._thread_spin = None

        # Status label
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: 12px; color: {Theme.MUTED};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton(t("backup.start"))
        self._start_btn.setFixedWidth(120)
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background: {Theme.ACCENT}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 16px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {Theme.ACCENT_HOVER}; }}"
            f"QPushButton:disabled {{ background: {Theme.PANEL_2}; color: {Theme.MUTED}; }}"
        )
        self._start_btn.clicked.connect(self._start_backup)
        btn_row.addWidget(self._start_btn)
        layout.addLayout(btn_row)

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
        errors = [r for r in results if r.get("error")]
        ok = [r for r in results if not r.get("error")]
        total_rows = sum(r.get("row_count", 0) for r in ok)
        msg = t("backup.done").format(count=len(ok), rows=f"{total_rows:,}")
        if errors:
            msg += f"\n{len(errors)} table(s) failed."
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

        # Header
        header = QHBoxLayout()
        title = QLabel(t("backup.manager"))
        title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {Theme.TEXT};")
        header.addWidget(title)
        header.addStretch()

        self._delete_btn = QPushButton(t("backup.delete"))
        self._delete_btn.setFixedWidth(80)
        self._delete_btn.setEnabled(False)
        self._delete_btn.setStyleSheet(
            f"QPushButton {{ background: {Theme.PANEL}; color: {Theme.TEXT};"
            f" border: 1px solid {Theme.BORDER}; border-radius: 6px; padding: 4px 12px; }}"
            f"QPushButton:hover {{ background: {Theme.PANEL_2}; }}"
            f"QPushButton:disabled {{ background: {Theme.PANEL}; color: {Theme.MUTED_2}; }}"
        )
        self._delete_btn.clicked.connect(self._delete_selected)
        header.addWidget(self._delete_btn)

        self._open_btn = QPushButton(t("backup.open_folder"))
        self._open_btn.setFixedWidth(100)
        self._open_btn.setEnabled(False)
        self._open_btn.setStyleSheet(self._delete_btn.styleSheet())
        self._open_btn.clicked.connect(self._open_folder)
        header.addWidget(self._open_btn)
        layout.addLayout(header)

        # Table
        columns = [
            t("backup.col.table"),
            t("backup.col.database"),
            t("backup.col.date"),
            t("backup.col.rows"),
            t("backup.col.size"),
            t("backup.col.format"),
        ]
        self._table = QTableWidget(0, len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setStyleSheet(
            f"QTableWidget {{ background: {Theme.SURFACE}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 8px; gridline-color: {Theme.BORDER}; }}"
            f"QTableWidget::item {{ padding: 4px 8px; }}"
            f"QTableWidget::item:selected {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}"
            f"QHeaderView::section {{ background: {Theme.PANEL}; color: {Theme.MUTED};"
            f" border: none; border-bottom: 1px solid {Theme.BORDER}; padding: 6px 8px;"
            f" font-weight: 500; }}"
        )
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        layout.addWidget(self._table)

        # Empty state
        self._empty = QLabel(t("backup.empty"))
        self._empty.setStyleSheet(f"color: {Theme.MUTED}; padding: 32px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty)

        self._records: list[Any] = []
        self.refresh()

    def refresh(self) -> None:
        from dbaide.backup import BackupRegistry
        registry = BackupRegistry()
        self._records = registry.list_backups()
        self._table.setRowCount(len(self._records))
        for row, rec in enumerate(self._records):
            self._table.setItem(row, 0, QTableWidgetItem(rec.table))
            self._table.setItem(row, 1, QTableWidgetItem(rec.database))
            self._table.setItem(row, 2, QTableWidgetItem(rec.timestamp))
            self._table.setItem(row, 3, QTableWidgetItem(f"{rec.row_count:,}"))
            self._table.setItem(row, 4, QTableWidgetItem(_fmt_size(rec.file_size)))
            self._table.setItem(row, 5, QTableWidgetItem(rec.format))
        has = len(self._records) > 0
        self._table.setVisible(has)
        self._empty.setVisible(not has)
        self._delete_btn.setEnabled(False)
        self._open_btn.setEnabled(False)

    def _on_selection(self) -> None:
        has = bool(self._table.selectionModel().hasSelection())
        self._delete_btn.setEnabled(has)
        self._open_btn.setEnabled(has)

    def _selected_record(self) -> Any | None:
        indexes = self._table.selectionModel().selectedRows()
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
