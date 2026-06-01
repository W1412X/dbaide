from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHeaderView,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.trace_model import TraceModel, TraceNode
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.theme import Theme

# Status glyphs so state is readable at a glance.
_GLYPH = {
    "running": "▶",
    "completed": "✓",
    "failed": "✗",
    "waiting": "⏸",
    "info": "·",
    "idle": "·",
    "done": "✓",
}
_NODE_ROLE = Qt.ItemDataRole.UserRole


class TracePanel(QWidget):
    """Execution-tree view of a run: a live summary on top, the tool/sub-agent tree
    in the middle (parallel work shown as sibling nodes), and a detail pane below
    that shows everything about whichever node you click."""

    event_selected = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        split = QSplitter(Qt.Orientation.Vertical)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["", "Step", "Status"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setWordWrap(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._tree.setFont(QFont("Menlo", 10))
        self._tree.itemClicked.connect(self._on_click)

        self._detail = QTextBrowser()
        self._detail.setFont(QFont("Menlo", 10))
        configure_readonly_text_view(self._detail)
        self._detail.setPlaceholderText("Click a step to inspect it.")
        self._detail.setMinimumHeight(80)

        split.addWidget(self._tree)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split)

        self._model: TraceModel | None = None
        self._selected_id: str = ""

    # ── Public API (preserved for callers) ───────────────────────────────────

    def load_events(self, events: list[dict[str, Any]]) -> None:
        model = TraceModel()
        for event in events or []:
            model.ingest(event)
        model.finalize()
        self._model = model
        self._render()

    def begin_live(self) -> None:
        self._model = TraceModel()
        self._selected_id = ""
        self._detail.clear()
        self._render()

    def append_live(self, message: str) -> None:
        if not message.strip():
            return
        text = message.strip()
        if text.startswith("[assets]"):
            self.append_live_event(
                {"stage": "build_assets", "title": text.replace("[assets]", "", 1).strip(),
                 "status": "running", "kind": "info"}
            )
        else:
            self.append_live_event({"stage": "agent", "title": text, "status": "running", "kind": "info"})

    def append_live_event(self, event: dict[str, Any]) -> None:
        if self._model is None:
            self.begin_live()
        assert self._model is not None
        self._model.ingest(event)
        self._render()

    def end_live(self) -> None:
        if self._model is not None:
            self._model.finalize()
        self._render()

    def clear_trace(self) -> None:
        self._model = None
        self._selected_id = ""
        self._tree.clear()
        self._detail.clear()

    def is_empty(self) -> bool:
        return self._model is None or not self._model.steps

    def copy_text(self) -> str:
        lines: list[str] = []

        def walk(item: QTreeWidgetItem, depth: int) -> None:
            text = " ".join(item.text(c) for c in range(3) if item.text(c)).strip()
            lines.append("  " * depth + text)
            for i in range(item.childCount()):
                walk(item.child(i), depth + 1)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i), 0)
        return "\n".join(lines)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        self._tree.clear()
        model = self._model
        if model is None:
            return

        summary = QTreeWidgetItem(["", model.summary_line(), ""])
        summary.setFont(1, _bold())
        summary.setForeground(1, _overall_color(model.overall))
        summary.setData(0, _NODE_ROLE, {"__summary__": True, "title": model.summary_line(),
                                        "status": model.overall})
        self._tree.addTopLevelItem(summary)

        last: QTreeWidgetItem = summary
        for node in model.steps:
            last = self._add_node(self._tree, node)

        self._tree.scrollToItem(last)
        # Re-show the previously selected node's detail (don't lose it on live re-render).
        if self._selected_id:
            data = self._find_node_data(self._selected_id)
            if data is not None:
                self._show_detail(data)

    def _add_node(self, parent, node: TraceNode) -> QTreeWidgetItem:
        glyph = _GLYPH.get(node.status, "·")
        is_tool = node.parent_id == "__root__"
        if is_tool:
            head = node.phase or node.stage
            if node.title and node.title not in head:
                head = f"{head} — {node.title}"
            indicator = f"{glyph} {node.step}" if node.step else glyph
        else:
            head = f"{node.agent_name}: {node.title}" if node.agent_name else node.title
            indicator = glyph
        status_text = f"{node.duration_ms:.0f} ms" if (node.status == "completed" and node.duration_ms > 0) else node.status

        item = QTreeWidgetItem([indicator, head, status_text])
        color = _status_color(node.status)
        item.setForeground(1, color)
        item.setForeground(2, color)
        item.setData(0, _NODE_ROLE, {
            "node_id": node.id, "stage": node.stage, "phase": node.phase,
            "agent": node.agent_name, "status": node.status, "title": node.title,
            "detail": node.detail, "duration_ms": node.duration_ms, "step": node.step,
            "thought": node.thought, "raw": node.raw,
        })

        if node.thought:
            t = QTreeWidgetItem(["", f"💭 {node.thought[:300]}", ""])
            t.setForeground(1, _muted())
            item.addChild(t)

        parent_is_tree = isinstance(parent, QTreeWidget)
        if parent_is_tree:
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)

        for child in node.children:
            self._add_node(item, child)

        item.setExpanded(True)
        return item

    def _find_node_data(self, node_id: str) -> dict | None:
        stack = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            data = item.data(0, _NODE_ROLE)
            if isinstance(data, dict) and data.get("node_id") == node_id:
                return data
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, _NODE_ROLE)
        if not isinstance(data, dict):
            return
        self._selected_id = str(data.get("node_id") or "")
        self._show_detail(data)
        self.event_selected.emit(data)

    def _show_detail(self, data: dict) -> None:
        self._detail.setPlainText(_format_detail(data))


def _format_detail(data: dict) -> str:
    if data.get("__summary__"):
        return str(data.get("title") or "")
    lines: list[str] = []
    title = str(data.get("title") or data.get("phase") or data.get("stage") or "step")
    lines.append(title)
    meta = []
    if data.get("phase"):
        meta.append(f"phase: {data['phase']}")
    if data.get("stage"):
        meta.append(f"stage: {data['stage']}")
    if data.get("agent"):
        meta.append(f"agent: {data['agent']}")
    if data.get("step"):
        meta.append(f"step: {data['step']}")
    meta.append(f"status: {data.get('status') or '?'}")
    if data.get("duration_ms"):
        meta.append(f"{float(data['duration_ms']):.0f} ms")
    lines.append(" · ".join(meta))
    if data.get("thought"):
        lines.append("")
        lines.append(f"thought: {data['thought']}")
    if data.get("detail"):
        lines.append("")
        lines.append(str(data["detail"]))
    raw = data.get("raw")
    if isinstance(raw, dict) and raw:
        # Surface useful payload keys explicitly, then the full event.
        for key in ("sql", "args"):
            if raw.get(key):
                lines.append("")
                lines.append(f"{key}: {raw[key]}")
        lines.append("")
        lines.append("─ raw event ─")
        try:
            lines.append(json.dumps(raw, ensure_ascii=False, indent=2, default=str))
        except (TypeError, ValueError):
            lines.append(str(raw))
    return "\n".join(lines)


def _bold() -> QFont:
    f = QFont("Menlo", 10)
    f.setBold(True)
    return f


def _overall_color(overall: str) -> QColor:
    return {"done": _green(), "failed": _red(), "running": _blue()}.get(overall, _blue())


def _status_color(status: str) -> QColor:
    return {
        "completed": _green(), "failed": _red(),
        "waiting": _yellow(), "running": _blue(), "info": _muted(),
    }.get(status, _blue())


def _red() -> QColor:
    return QColor(Theme.RED)


def _blue() -> QColor:
    return QColor(Theme.BLUE)


def _green() -> QColor:
    return QColor(Theme.GREEN)


def _yellow() -> QColor:
    return QColor(Theme.YELLOW)


def _muted() -> QColor:
    return QColor(Theme.MUTED)
