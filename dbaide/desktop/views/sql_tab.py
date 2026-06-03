from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_multiline_text_edit, configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme


class SqlTab(QWidget):
    run_requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        from dbaide.i18n import t
        self._t = t

        # ── Editor ────────────────────────────────────────────────────────────
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(t("sql.placeholder"))
        self.editor.setFont(QFont("Menlo", 11))
        configure_multiline_text_edit(self.editor, min_height=120, max_height=480, padding=16)
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 10px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {Theme.FOCUS}; }}"
        )
        self.editor.installEventFilter(self)  # ⌘↵ to run
        layout.addWidget(self.editor, 2)

        # ── Run row: a quiet ⌘↵ hint on the left, the primary Run on the right
        # (mirrors the chat composer's bottom-right send). ──────────────────────
        run_row = QHBoxLayout()
        run_row.setContentsMargins(2, 0, 2, 0)
        hint = QLabel(t("sql.run_hint"))
        hint.setStyleSheet(f"color: {Theme.MUTED_2}; font-size: 11px; background: transparent;")
        run_row.addWidget(hint)
        run_row.addStretch(1)
        self.run_btn = compact_button(t("sql.run"), primary=True, width=92)
        self.run_btn.setIcon(svg_icon("play", color="#ffffff", size=13))
        self.run_btn.setIconSize(QSize(13, 13))
        self.run_btn.setToolTip(t("sql.run_tooltip"))
        self.run_btn.clicked.connect(self._run)
        run_row.addWidget(self.run_btn)
        layout.addLayout(run_row)
        self._busy = BusyAnimator(lambda: self.run_btn.setIcon(spinner_icon(self._busy.angle, color="#ffffff")))

        # ── Results ───────────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.tabBar().setProperty("panelTabs", True)  # quiet, rounded tabs
        self.tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {Theme.BORDER_SOFT}; border-radius: 10px;"
            f" top: -1px; background: {Theme.SURFACE}; }}"
        )
        self.result_table = ResultTableWidget()
        self.messages = QTextBrowser()
        self.messages.setFont(QFont("Menlo", 10))
        configure_readonly_text_view(self.messages)
        # Borderless — it's a tab page inside the bordered pane (no frame-in-a-frame).
        self.messages.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.tabs.addTab(self.result_table, t("sql.result"))
        self.tabs.addTab(self.messages, t("sql.messages"))
        layout.addWidget(self.tabs, 1)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt signature)
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            mod = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
            )
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mod:
                self._run()
                return True
        return super().eventFilter(obj, event)

    def _run(self) -> None:
        if self.run_btn.isEnabled():
            self.run_requested.emit(self.editor.toPlainText(), "execute")

    def set_running(self, running: bool) -> None:
        if running:
            self.run_btn.setText(self._t("sql.running"))
            self._busy.start()
        else:
            self._busy.stop()
            self.run_btn.setIcon(svg_icon("play", color="#ffffff", size=13))
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
