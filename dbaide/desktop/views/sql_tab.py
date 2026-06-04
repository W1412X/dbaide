from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.sql_editor import SqlEditor
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
        layout.setSpacing(12)
        from dbaide.i18n import t
        self._t = t

        # ── Editor + a vertical strip of small action icons on its right edge ───
        editor_row = QHBoxLayout()
        editor_row.setContentsMargins(0, 0, 0, 0)
        editor_row.setSpacing(6)
        self.editor = SqlEditor()
        self.editor.setPlaceholderText(t("sql.placeholder"))
        self.editor.setFont(QFont("Menlo", 11))
        configure_multiline_text_edit(self.editor, min_height=120, max_height=480, padding=16)
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER};"
            f" border-radius: 10px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {Theme.FOCUS}; }}"
        )
        self.editor.installEventFilter(self)  # ⌘↵ to run
        from dbaide.desktop.components.sql_highlighter import SqlHighlighter
        self._highlighter = SqlHighlighter(self.editor.document())
        editor_row.addWidget(self.editor, 1)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        # Run is the accented primary action; Explain (execution plan) and Format are
        # quiet secondary icons. The group is bottom-aligned so it does not crowd the
        # workbench corner controls above the editor.
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
        # The editor auto-grows with its content (composer-style); give the row its
        # natural height and let the results grid below take the remaining space, so
        # there's no dead gap around a short query.
        layout.addLayout(editor_row)
        self._busy = BusyAnimator(
            lambda: self.run_btn.setIcon(spinner_icon(self._busy.angle, color=Theme.ACCENT_TEXT, size=15))
        )

        # ── Results ───────────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.tabBar().setProperty("panelTabs", True)  # quiet, rounded tabs
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tabs.setStyleSheet(
            f"QTabWidget {{ background: {Theme.SURFACE}; }}"
            f"QTabWidget::tab-bar {{ background: {Theme.SURFACE}; }}"
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

    def _icon_button(self, icon_name: str, tooltip: str, *, primary: bool = False) -> QToolButton:
        """A small square icon button for the editor's right-edge action strip.

        The global QToolButton rule (padding:0 10px; min/max-height:26px; border)
        otherwise distorts these into 30×26 rects and squeezes the icon, so the box
        size is pinned explicitly here."""
        btn = QToolButton()
        color = Theme.ACCENT_TEXT if primary else Theme.TEXT_2
        btn.setIcon(svg_icon(icon_name, color=color, size=14))
        btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(26, 26)
        box = ("padding: 0; margin: 0; min-width: 26px; max-width: 26px;"
               " min-height: 26px; max-height: 26px; border: none; border-radius: 7px;")
        if primary:
            btn.setStyleSheet(
                f"QToolButton {{ background: {Theme.ACCENT}; {box} }}"
                f"QToolButton:hover {{ background: {Theme.ACCENT_HOVER}; }}"
                f"QToolButton:disabled {{ background: {Theme.PANEL_2}; }}"
            )
        else:
            btn.setStyleSheet(
                f"QToolButton {{ background: {Theme.PANEL_2}; {box} }}"
                f"QToolButton:hover {{ background: {Theme.PANEL_3}; }}"
                f"QToolButton:disabled {{ background: {Theme.PANEL}; }}"
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
            # ⌘⇧F / Ctrl+Shift+F — format the editor contents.
            if event.key() == Qt.Key.Key_F and mod and (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._format()
                return True
        return super().eventFilter(obj, event)

    def _current_sql(self) -> str:
        """What Run executes: the highlighted selection if any, else the statement
        under the cursor (so multi-statement editors 'just run' the right one)."""
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
            self.run_btn.setIcon(svg_icon("play", color=Theme.ACCENT_TEXT, size=15))
            self.run_btn.setToolTip(f"{self._t('sql.run')} · ⌘↵")
        self.run_btn.setEnabled(not running)
        self.explain_btn.setEnabled(not running)
        self.format_btn.setEnabled(not running)
        self.editor.setEnabled(not running)

    def set_sql(self, sql: str) -> None:
        self.editor.setPlainText(sql)

    def set_completions(self, names: list[str]) -> None:
        self.editor.set_completions(names)

    def set_schema(self, schema: dict) -> None:
        self.editor.set_schema(schema)

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
