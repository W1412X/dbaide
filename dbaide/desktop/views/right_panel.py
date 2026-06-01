from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.markdown import MarkdownView
from dbaide.desktop.components.panel_header import PanelHeader
from dbaide.desktop.components.trace import TracePanel
from dbaide.desktop.dialogs.history import HistoryDialog
from dbaide.desktop.dialogs.joins import JoinsDialog
from dbaide.desktop.views.history_tab import HistoryTab
from dbaide.desktop.views.joins_tab import JoinsTab


class RightPanel(QWidget):
    copy_trace_requested = pyqtSignal()
    clear_trace_requested = pyqtSignal()
    clear_conversation_requested = pyqtSignal()
    history_selected = pyqtSignal(str)
    history_preview = pyqtSignal(str)
    joins_refresh_requested = pyqtSignal()
    joins_add_requested = pyqtSignal(dict)
    joins_update_requested = pyqtSignal(dict)
    joins_delete_requested = pyqtSignal(str)

    _TAB_INSPECTOR = 0
    _TAB_TRACE = 1
    _TAB_PLAN = 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        from dbaide.i18n import t
        self.header = PanelHeader((t("panel.inspector"), t("panel.trace"), t("panel.plan")))
        self.header.tab_changed.connect(self._switch_tab)
        self.header.history_clicked.connect(self.open_history)
        self.header.joins_clicked.connect(self.open_joins)
        self.header.copy_trace_requested.connect(self.copy_trace_requested.emit)
        self.header.clear_trace_requested.connect(self.clear_trace_requested.emit)
        self.header.clear_conversation_requested.connect(self.clear_conversation_requested.emit)
        layout.addWidget(self.header)

        content_frame = QFrame()
        content_frame.setProperty("panelContent", True)
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.trace = TracePanel()
        self.plan_view = QTextBrowser()
        self.plan_view.setFontFamily("Menlo")
        configure_readonly_text_view(self.plan_view)
        self.inspect_preview = MarkdownView()
        self.inspect_json = QTextBrowser()
        self.inspect_json.setFontFamily("Menlo")
        configure_readonly_text_view(self.inspect_json)
        inspect = QWidget()
        inspect_layout = QVBoxLayout(inspect)
        inspect_layout.setContentsMargins(0, 0, 0, 0)
        inspect_layout.addWidget(self.inspect_preview, 2)
        inspect_layout.addWidget(self.inspect_json, 1)
        self.history = HistoryTab()
        self.joins = JoinsTab()
        self._history_dialog: HistoryDialog | None = None
        self._joins_dialog: JoinsDialog | None = None
        self.stack.addWidget(inspect)
        self.stack.addWidget(self.trace)
        self.stack.addWidget(self.plan_view)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content_frame, 1)

    def _switch_tab(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    def _history_popup(self) -> HistoryDialog:
        if self._history_dialog is None:
            dialog = HistoryDialog(self.history, parent=self.window())
            dialog.history_selected.connect(self.history_selected.emit)
            dialog.history_preview.connect(self.history_preview.emit)
            self._history_dialog = dialog
        return self._history_dialog

    def _joins_popup(self) -> JoinsDialog:
        if self._joins_dialog is None:
            dialog = JoinsDialog(self.joins, parent=self.window())
            dialog.refresh_requested.connect(self.joins_refresh_requested.emit)
            dialog.add_requested.connect(self.joins_add_requested.emit)
            dialog.update_requested.connect(self.joins_update_requested.emit)
            dialog.delete_requested.connect(self.joins_delete_requested.emit)
            self._joins_dialog = dialog
        return self._joins_dialog

    def open_history(self) -> None:
        dialog = self._history_popup()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_joins(self) -> None:
        dialog = self._joins_popup()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_trace(self, events: list[dict[str, Any]]) -> None:
        self.trace.load_events(events)

    def show_plan(self, result: dict[str, Any]) -> None:
        plan = result.get("query_plan") or {}
        validation = result.get("validation_report") or {}
        lines = ["Query Plan", ""]
        if plan.get("intent_summary"):
            lines.append(f"Intent: {plan['intent_summary']}")
        if plan.get("target_entities"):
            lines.append(f"Tables: {', '.join(plan['target_entities'])}")
        if plan.get("selected_columns"):
            lines.append(f"Columns: {', '.join(plan['selected_columns'])}")
        if plan.get("filters"):
            lines.append("Filters:")
            lines.extend(f"  - {f}" for f in plan["filters"])
        if plan.get("assumptions"):
            lines.append("Assumptions:")
            lines.extend(f"  - {a}" for a in plan["assumptions"])
        if plan.get("confidence"):
            lines.append(f"Confidence: {plan['confidence']}")
        if validation:
            lines.extend(["", "Validation:", json.dumps(validation, ensure_ascii=False, indent=2)])
        self.plan_view.setPlainText("\n".join(lines))

    def show_inspector(
        self,
        *,
        markdown: str = "",
        doc: dict[str, Any] | None = None,
        focus: bool = True,
    ) -> None:
        self.inspect_preview.clear_view()
        if markdown:
            self.inspect_preview.append_card("Asset Preview", markdown)
        if doc:
            self.inspect_json.setPlainText(json.dumps(doc, ensure_ascii=False, indent=2))
        if focus:
            self.header.set_current_tab(self._TAB_INSPECTOR)
            self._switch_tab(self._TAB_INSPECTOR)

    def show_search_hits(self, query: str, hits: list[dict[str, Any]]) -> None:
        self.inspect_preview.clear_view()
        self.inspect_json.clear()
        if not hits:
            self.inspect_preview.append_card(
                "Asset search",
                f"No matches for `{query}`. Build assets or ask in natural language on the Ask tab.",
            )
        else:
            lines = [f"Found **{len(hits)}** matches for `{query}`:", ""]
            for hit in hits:
                lines.append(f"- **{hit.get('path')}** ({hit.get('kind')}, score {hit.get('score', 0):.1f})")
                if hit.get("summary"):
                    lines.append(f"  {str(hit['summary'])[:160]}")
            self.inspect_preview.append_card("Asset search", "\n".join(lines))
        self.header.set_current_tab(self._TAB_INSPECTOR)
        self._switch_tab(self._TAB_INSPECTOR)

    def load_history(self, entries: list[dict[str, Any]]) -> None:
        self.history.load(entries)

    def focus_trace(self) -> None:
        self.header.set_current_tab(self._TAB_TRACE)
        self._switch_tab(self._TAB_TRACE)

    def focus_history(self) -> None:
        self.open_history()

    def focus_joins(self) -> None:
        self.open_joins()

    def show_joins(self, records: list[dict[str, Any]]) -> None:
        self.joins.load(records)

    def clear_all(self) -> None:
        self.trace.clear_trace()
        self.plan_view.clear()
        self.inspect_preview.clear_view()
        self.inspect_json.clear()
