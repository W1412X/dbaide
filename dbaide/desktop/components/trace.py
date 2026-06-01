from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt
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

from dbaide.agent.progress_events import STEP_TYPE_LABELS
from dbaide.agent.trace_model import TraceModel, TraceNode
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator
from dbaide.desktop.theme import Theme

# Types that get a leading category chip in the tree (plain tool/substep don't —
# their phase label already says what they are).
_CHIP_TYPES = frozenset({"sql", "phase", "llm", "decision", "io"})

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        split = QSplitter(Qt.Orientation.Vertical)
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderHidden(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setWordWrap(True)
        self._tree.setUniformRowHeights(False)
        self._tree.setIndentation(16)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setFont(QFont("Inter", 11))
        self._tree.itemClicked.connect(self._on_click)

        self._detail = QTextBrowser()
        self._detail.setFont(QFont("Menlo", 10))
        configure_readonly_text_view(self._detail)
        self._detail.setPlaceholderText("Click a step to inspect it.")
        self._detail.setMinimumHeight(96)

        split.addWidget(self._tree)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split)

        self._model: TraceModel | None = None
        self._selected_id: str = ""
        # Running rows show a spinning circle (instead of a static ▶) until they
        # resolve. We update just those rows' glyph on each tick — no full re-render.
        self._running_items: list[tuple[QTreeWidgetItem, int]] = []
        self._busy = BusyAnimator(self._on_spin_frame)

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
        self._running_items = []
        self._busy.stop()
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
        self._running_items = []
        model = self._model
        if model is None:
            self._busy.stop()
            return

        summary = QTreeWidgetItem(["", model.summary_line(), ""])
        summary.setFont(1, _semibold())
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
        # Spin while anything is still running; stop once everything has resolved.
        if self._running_items:
            self._busy.start()
        else:
            self._busy.stop()

    def _on_spin_frame(self, frame: str) -> None:
        for item, step in self._running_items:
            try:
                item.setText(0, f"{frame} {step}" if step else frame)
            except RuntimeError:
                pass  # item deleted by a concurrent re-render

    def _add_node(self, parent, node: TraceNode) -> QTreeWidgetItem:
        running = node.status == "running"
        glyph = self._busy.frame if running else _GLYPH.get(node.status, "·")
        is_tool = node.parent_id == "__root__"
        if is_tool:
            # Step row: bright phase name, status glyph carries colour, duration muted.
            indicator = f"{glyph} {node.step}" if node.step else glyph
            head = _head_text(node)
            if node.duration_ms > 0 and node.status in ("completed", "failed"):
                status_text = _fmt_ms(node.duration_ms)
            elif node.status in ("running", "waiting"):
                status_text = node.status
            else:
                status_text = ""
            item = QTreeWidgetItem([indicator, head, status_text])
            item.setForeground(0, _status_color(node.status))
            item.setForeground(1, _red() if node.status == "failed" else _bright())
            item.setForeground(2, _red() if node.status == "failed" else _muted())
            item.setFont(1, _semibold())
        else:
            # Sub-agent row: muted "label: detail", glyph shows status (no "completed" word).
            indicator = glyph
            head = f"{node.agent_name} · {node.title}" if node.agent_name else node.title
            item = QTreeWidgetItem([indicator, head, ""])
            item.setForeground(0, _status_color(node.status))
            item.setForeground(1, _muted())

        item.setData(0, _NODE_ROLE, {
            "node_id": node.id, "stage": node.stage, "phase": node.phase,
            "agent": node.agent_name, "status": node.status, "title": node.title,
            "detail": node.detail, "duration_ms": node.duration_ms, "step": node.step,
            "thought": node.thought, "node_type": node.node_type, "raw": node.raw,
        })
        if running:
            self._running_items.append((item, node.step if is_tool else 0))

        parent_is_tree = isinstance(parent, QTreeWidget)
        if parent_is_tree:
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)

        if node.thought:
            self._add_leaf(item, f"💭 {node.thought}", muted=True)
        # Surface the useful result/summary (not the boilerplate "Calling X / X done").
        if is_tool:
            secondary = _secondary_text(node)
            if secondary:
                self._add_leaf(item, secondary, muted=True)

        for child in node.children:
            self._add_node(item, child)

        item.setExpanded(True)
        return item

    def _add_leaf(self, parent: QTreeWidgetItem, text: str, *, muted: bool = True) -> None:
        leaf = QTreeWidgetItem(["", text[:160], ""])
        leaf.setForeground(1, _muted() if muted else _bright())
        leaf.setFirstColumnSpanned(True)
        parent.addChild(leaf)

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

    def _show_detail(self, data: dict) -> None:
        self._detail.setPlainText(_format_detail(data))


def _head_text(node: TraceNode) -> str:
    """Step head with a leading category chip for the 'special' step types."""
    base = node.phase or node.stage or node.title
    chip = STEP_TYPE_LABELS.get(node.node_type, "")
    if chip and node.node_type in _CHIP_TYPES:
        return f"{chip} · {base}"
    return base


def _format_detail(data: dict) -> str:
    if data.get("__summary__"):
        return str(data.get("title") or "")
    node_type = str(data.get("node_type") or "info")
    lines: list[str] = []
    title = str(data.get("title") or data.get("phase") or data.get("stage") or "step")
    lines.append(title)
    meta = [f"type: {node_type}"]
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

    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}

    # SQL steps lead with the query itself — that is the whole point of the step.
    if node_type == "sql":
        sql = str(raw.get("sql") or data.get("detail") or "").strip()
        facts = []
        if raw.get("row_count") not in (None, ""):
            facts.append(f"{raw['row_count']} rows")
        if raw.get("database"):
            facts.append(f"db={raw['database']}")
        if facts:
            lines.append("")
            lines.append(" · ".join(facts))
        if sql:
            lines.append("")
            lines.append("─ SQL ─")
            lines.append(sql)
    else:
        if data.get("thought"):
            lines.append("")
            lines.append(f"thought: {data['thought']}")
        if data.get("detail"):
            lines.append("")
            lines.append(str(data["detail"]))

    if raw:
        # For non-SQL steps also surface explicit payload keys, then the full event.
        if node_type != "sql":
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


def _secondary_text(node: TraceNode) -> str:
    """The useful one-line summary for a tool step: its result detail, or a
    non-boilerplate title. Returns '' when there's nothing worth a second line
    (the loop's "Calling X" / "X done" frames carry no information)."""
    detail = (node.detail or "").strip()
    if detail:
        return " ".join(detail.split())
    title = (node.title or "").strip()
    if not title or title.startswith("Calling ") or title.endswith("done"):
        return ""
    return " ".join(title.split())


def _fmt_ms(ms: float) -> str:
    return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def _semibold() -> QFont:
    f = QFont("Inter", 11)
    f.setWeight(QFont.Weight.DemiBold)
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


def _bright() -> QColor:
    return QColor(Theme.TEXT)
