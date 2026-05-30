from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import configure_multiline_text_edit, configure_readonly_text_view
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme


class SqlTab(QWidget):
    run_requested = pyqtSignal(str, str)
    validate_requested = pyqtSignal(str)
    explain_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.run_btn = compact_button("Run", primary=True, width=72)
        self.run_btn.setToolTip("Run read-only query")
        self.run_btn.clicked.connect(lambda: self.run_requested.emit(self.editor.toPlainText(), "execute"))
        toolbar.addWidget(self.run_btn)
        self.more = MenuButton("More ▾", max_width=88)
        self.more.add_action("Validate SQL", lambda: self.validate_requested.emit(self.editor.toPlainText()))
        self.more.add_action("Explain SQL", lambda: self.explain_requested.emit(self.editor.toPlainText()))
        toolbar.addWidget(self.more)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Paste SQL here. Only single read-only statements are allowed.")
        self.editor.setFont(QFont("Menlo", 11))
        configure_multiline_text_edit(self.editor, min_height=120, max_height=480, padding=16)
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER}; border-radius: 8px; }}"
        )
        layout.addWidget(self.editor, 2)
        self.tabs = QTabWidget()
        self.result_table = ResultTableWidget()
        self.messages = QTextBrowser()
        self.explain_view = QTextBrowser()
        self.validation_view = QTextBrowser()
        for view in (self.messages, self.explain_view, self.validation_view):
            view.setFont(QFont("Menlo", 10))
            configure_readonly_text_view(view)
        self.tabs.addTab(self.result_table, "Result")
        self.tabs.addTab(self.messages, "Messages")
        self.tabs.addTab(self.explain_view, "Explain")
        self.tabs.addTab(self.validation_view, "Validation")
        layout.addWidget(self.tabs, 1)

    def set_sql(self, sql: str) -> None:
        self.editor.setPlainText(sql)

    def show_validation(self, payload: dict) -> None:
        lines = []
        if payload.get("ok"):
            lines.append("Validation passed")
        else:
            lines.append("Validation failed")
        for issue in payload.get("issues") or []:
            lines.append(f"- [{issue.get('severity')}] {issue.get('message')}")
        if payload.get("normalized_sql"):
            lines.extend(["", "Normalized SQL:", payload["normalized_sql"]])
        self.validation_view.setPlainText("\n".join(lines))
        self.tabs.setCurrentWidget(self.validation_view)

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

    def show_explain(self, payload: dict) -> None:
        self.explain_view.setPlainText(str(payload))
        self.tabs.setCurrentWidget(self.explain_view)

    def show_error(self, message: str) -> None:
        self.messages.setPlainText(message)
        self.tabs.setCurrentWidget(self.messages)
