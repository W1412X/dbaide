from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.sql_editor import SqlEditor
from dbaide.desktop.components.inputs import configure_readonly_text_view, configure_sql_editor_pane
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
from dbaide.desktop.components.table import ResultTableWidget
from dbaide.desktop.theme import Theme, workbench_tab_stylesheet


class SqlTab(QWidget):
    run_requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        from dbaide.i18n import t
        self._t = t

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)

        editor_wrap = QWidget()
        editor_wrap.setObjectName("sqlEditorPane")
        editor_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        editor_wrap.setStyleSheet(
            f"QWidget#sqlEditorPane {{ background: {Theme.SURFACE};"
            f" border: 1px solid {Theme.BORDER_SOFT}; border-radius: {Theme.RADIUS_MD}px; }}"
        )
        editor_row = QHBoxLayout(editor_wrap)
        editor_row.setContentsMargins(8, 8, 8, 8)
        editor_row.setSpacing(6)

        self.editor = SqlEditor()
        self.editor.setPlaceholderText(t("sql.placeholder"))
        self.editor.setFont(QFont("Menlo", 11))
        configure_sql_editor_pane(self.editor, min_height=100)
        self.editor.setStyleSheet(
            "QPlainTextEdit { background: transparent; border: none; }"
            "QPlainTextEdit:focus { border: none; }"
        )
        self.editor.installEventFilter(self)
        from dbaide.desktop.components.sql_highlighter import SqlHighlighter
        self._highlighter = SqlHighlighter(self.editor.document())
        editor_row.addWidget(self.editor, 1)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.run_btn = self._icon_button("play", f"{t('sql.run')} · ⌘↵", primary=True)
        self.run_btn.clicked.connect(self._run)
        self.explain_btn = self._icon_button("list-tree", t("sql.explain_tooltip"))
        self.explain_btn.clicked.connect(self._explain)
        self.format_btn = self._icon_button("sparkles", f"{t('sql.format')} · ⌘⇧F")
        self.format_btn.clicked.connect(self._format)
        actions.addStretch(1)
        actions.addWidget(self.format_btn)
        actions.addWidget(self.explain_btn)
        actions.addWidget(self.run_btn)
        editor_row.addLayout(actions)
        self._splitter.addWidget(editor_wrap)

        self._busy = BusyAnimator(
            lambda: self.run_btn.setIcon(spinner_icon(self._busy.angle, color=Theme.ACCENT, size=15)),
            parent=self,
        )

        self.tabs = QTabWidget()
        self.tabs.tabBar().setProperty("panelTabs", True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tabs.setStyleSheet(workbench_tab_stylesheet(bordered_pane=True))
        self.result_table = ResultTableWidget()
        self.messages = QTextBrowser()
        self.messages.setFont(QFont("Menlo", 10))
        configure_readonly_text_view(self.messages)
        self.messages.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.tabs.addTab(self.result_table, t("sql.result"))
        self.tabs.addTab(self.messages, t("sql.messages"))
        self._splitter.addWidget(self.tabs)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([240, 420])
        layout.addWidget(self._splitter, 1)

    def _icon_button(self, icon_name: str, tooltip: str, *, primary: bool = False) -> QToolButton:
        btn = QToolButton()
        color = Theme.ACCENT if primary else Theme.TEXT_2
        btn.setIcon(svg_icon(icon_name, color=color, size=14))
        btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(26, 26)
        box = ("padding: 0; margin: 0; min-width: 26px; max-width: 26px;"
               " min-height: 26px; max-height: 26px; border: none; border-radius: 7px;")
        hover = Theme.PANEL_2
        pressed = Theme.PANEL_3
        btn.setStyleSheet(
            f"QToolButton {{ background: transparent; {box} }}"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:pressed {{ background: {pressed}; }}"
            f"QToolButton:disabled {{ background: transparent; }}"
        )
        return btn

    def eventFilter(self, obj, event):  # noqa: N802 (Qt signature)
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            mod = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
            )
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mod:
                self._run()
                return True
            if event.key() == Qt.Key.Key_F and mod and (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._format()
                return True
        return super().eventFilter(obj, event)

    def _current_sql(self) -> str:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            selected = cursor.selectedText().replace(" ", "\n").strip()
            if selected:
                return selected
        from dbaide.rendering.sql_format import statement_at
        text = self.editor.toPlainText()
        return statement_at(text, cursor.position())

    def _run(self) -> None:
        if self.run_btn.isEnabled():
            self.run_requested.emit(self._current_sql(), "execute")

    def _explain(self) -> None:
        if self.explain_btn.isEnabled():
            self.run_requested.emit(self._current_sql(), "explain")

    def _format(self) -> None:
        from dbaide.rendering.sql_format import format_sql
        text = self.editor.toPlainText()
        formatted = format_sql(text)
        if formatted and formatted != text:
            self.editor.setPlainText(formatted)

    def set_running(self, running: bool) -> None:
        if running:
            self.run_btn.setToolTip(self._t("sql.running"))
            self._busy.start()
        else:
            self._busy.stop()
            self.run_btn.setIcon(svg_icon("play", color=Theme.ACCENT, size=15))
            self.run_btn.setToolTip(f"{self._t('sql.run')} · ⌘↵")
        self.run_btn.setEnabled(not running)
        self.explain_btn.setEnabled(not running)
        self.format_btn.setEnabled(not running)
        self.editor.setEnabled(not running)

    def set_sql(self, sql: str) -> None:
        self.editor.setPlainText(sql)

    def set_schema(self, schema: dict) -> None:
        self.editor.set_schema(schema)
        dialect = str((schema or {}).get("dialect") or "")
        if dialect:
            self.set_dialect(dialect)

    def set_dialect(self, dialect: str) -> None:
        self.editor.set_dialect(dialect)
        self._highlighter.set_dialect(dialect)

    def show_result(self, payload: dict) -> None:
        from dbaide.i18n import t
        truncated = bool(payload.get("truncated"))
        self.result_table.load(
            columns=payload.get("columns") or [],
            rows=payload.get("rows") or [],
            row_count=payload.get("row_count") or 0,
            truncated=truncated,
            elapsed_ms=float(payload.get("elapsed_ms") or 0),
        )
        elapsed = float(payload.get("elapsed_ms") or 0)
        msg = t("sql.executed_in", ms=f"{elapsed:.0f}")
        if truncated:
            msg += f"\n\n{t('sql.result_truncated')}"
        self.messages.setPlainText(msg)
        self.tabs.setCurrentWidget(self.result_table)

    def show_error(self, message: str) -> None:
        self.messages.setPlainText(message)
        self.tabs.setCurrentWidget(self.messages)
