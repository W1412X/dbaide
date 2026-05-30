from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.markdown import MarkdownView
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.components.trace import TracePanel


class RightPanel(QWidget):
    copy_trace_requested = pyqtSignal()
    clear_trace_requested = pyqtSignal()
    clear_conversation_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
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
        self.tabs.addTab(self.trace, "Trace")
        self.tabs.addTab(self.plan_view, "Plan")
        self.tabs.addTab(inspect, "Inspector")
        self._menu = MenuButton("⋯")
        self._menu.setFixedWidth(32)
        self._menu.add_action("Copy Trace", self.copy_trace_requested.emit)
        self._menu.add_action("Clear Trace", self.clear_trace_requested.emit)
        self._menu.add_action("Clear Conversation", self.clear_conversation_requested.emit)
        self.tabs.setCornerWidget(self._menu, Qt.Corner.TopRightCorner)
        layout.addWidget(self.tabs, 1)

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
            self.tabs.setCurrentIndex(2)

    def clear_all(self) -> None:
        self.trace.clear_trace()
        self.plan_view.clear()
        self.inspect_preview.clear_view()
        self.inspect_json.clear()
