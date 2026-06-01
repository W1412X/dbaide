from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import configure_multiline_text_edit, configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme


class SqlTab(QWidget):
    run_requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        from dbaide.i18n import t
        self._t = t
        toolbar = QHBoxLayout()
        # Run is the only action: validation/explain happen automatically and any
        # problem surfaces as an execution error in Messages — no separate buttons.
        self.run_btn = compact_button(t("sql.run"), primary=True, width=84)
        self.run_btn.setToolTip(t("sql.run_tooltip"))
        self.run_btn.clicked.connect(lambda: self.run_requested.emit(self.editor.toPlainText(), "execute"))
        toolbar.addWidget(self.run_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self._busy = BusyAnimator(lambda f: self.run_btn.setText(f"{f} {self._t('sql.running')}"))
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(t("sql.placeholder"))
        self.editor.setFont(QFont("Menlo", 11))
        configure_multiline_text_edit(self.editor, min_height=120, max_height=480, padding=16)
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER}; border-radius: 8px; }}"
        )
        layout.addWidget(self.editor, 2)
        self.tabs = QTabWidget()
        self.result_table = ResultTableWidget()
        self.messages = QTextBrowser()
        self.messages.setFont(QFont("Menlo", 10))
        configure_readonly_text_view(self.messages)
        self.tabs.addTab(self.result_table, "Result")
        self.tabs.addTab(self.messages, "Messages")
        layout.addWidget(self.tabs, 1)

    def set_running(self, running: bool) -> None:
        if running:
            self._busy.start()
        else:
            self._busy.stop()
            self.run_btn.setText(self._t("sql.run"))
        self.run_btn.setEnabled(not running)
        self.editor.setEnabled(not running)

    def set_sql(self, sql: str) -> None:
        self.editor.setPlainText(sql)

    def show_result(self, payload: dict) -> None:
        self.result_table.load(
            columns=payload.get("columns") or [],
            rows=payload.get("rows") or [],
            row_count=payload.get("row_count") or 0,
            truncated=bool(payload.get("truncated")),
            elapsed_ms=float(payload.get("elapsed_ms") or 0),
        )
        self.messages.setPlainText(f"Executed in {payload.get('elapsed_ms', 0):.0f}ms")
        self.tabs.setCurrentWidget(self.result_table)

    def show_error(self, message: str) -> None:
        self.messages.setPlainText(message)
        self.tabs.setCurrentWidget(self.messages)
