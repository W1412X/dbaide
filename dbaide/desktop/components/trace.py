from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.progress_events import STEP_TYPE_LABELS
from dbaide.agent.trace_model import TraceModel, TraceNode
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
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

        # Detail pane: a header with a "Copy raw" action over a formatted (HTML) view.
        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(0, 6, 0, 0)
        detail_layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(f"color:{Theme.TEXT_2}; font-size:11px; font-weight:600;")
        header.addWidget(self._detail_title)
        header.addStretch(1)
        self._copy_raw_btn = QToolButton()
        self._copy_raw_btn.setText("Copy raw")
        self._copy_raw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_raw_btn.setStyleSheet(
            f"QToolButton {{ color:{Theme.MUTED}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:6px; padding:2px 8px; font-size:11px; }}"
            f"QToolButton:hover {{ color:{Theme.TEXT}; }}"
        )
        self._copy_raw_btn.clicked.connect(self._copy_raw)
        self._copy_raw_btn.setVisible(False)
        header.addWidget(self._copy_raw_btn)
        detail_layout.addLayout(header)
        self._detail = QTextBrowser()
        self._detail.setFont(QFont("Inter", 11))
        configure_readonly_text_view(self._detail)
        self._detail.setPlaceholderText("Click a step to inspect it.")
        self._detail.setMinimumHeight(96)
        detail_layout.addWidget(self._detail, 1)
        self._raw_text = ""  # original event JSON for the current node (for Copy raw)

        split.addWidget(self._tree)
        split.addWidget(detail_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split)

        self._model: TraceModel | None = None
        self._selected_id: str = ""
        # Running rows show a spinning circle (instead of a static ▶) until they
        # resolve. We update just those rows' glyph on each tick — no full re-render.
        self._running_items: list[QTreeWidgetItem] = []
        self._busy = BusyAnimator(self._on_spin)
        # Live builds can fire many events per second; coalesce re-renders so the
        # tree stays smooth instead of rebuilding on every single event.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._render)

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
        if not self._render_timer.isActive():
            self._render_timer.start()  # coalesce bursts into one render per ~60ms

    def end_live(self) -> None:
        self._render_timer.stop()
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
        """A readable, structured export of the whole run — every step indented by
        depth with its status, duration, detail and the exact SQL it ran. Built from
        the model (not the visible rows) so nothing is elided."""
        model = self._model
        if model is None or not model.steps:
            return ""
        lines: list[str] = [model.summary_line(), ""]
        glyphs = {"completed": "✓", "failed": "✗", "running": "▶", "waiting": "⏸"}

        def walk(node: TraceNode, depth: int) -> None:
            indent = "  " * depth
            glyph = glyphs.get(node.status, "·")
            dur = f"  [{_fmt_ms(node.duration_ms)}]" if node.duration_ms else ""
            lines.append(f"{indent}{glyph} {_node_head(node)}{dur}")
            if node.thought:
                lines.append(f"{indent}    thought: {node.thought}")
            raw = node.raw if isinstance(node.raw, dict) else {}
            sql = str(raw.get("sql") or "").strip()
            if sql:
                for ln in sql.splitlines():
                    lines.append(f"{indent}    {ln}")
            else:
                detail = (node.detail or "").strip()
                if detail and detail not in _node_head(node):
                    lines.append(f"{indent}    {detail}")
            for child in node.children:
                walk(child, depth + 1)

        for node in model.steps:
            walk(node, 0)
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
            last = self._add_node(self._tree, node, depth=0)

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

    def _on_spin(self) -> None:
        icon = spinner_icon(self._busy.angle, color=Theme.BLUE)
        for item in self._running_items:
            try:
                item.setIcon(0, icon)
            except RuntimeError:
                pass  # item deleted by a concurrent re-render

    def _add_node(self, parent, node: TraceNode, *, depth: int) -> QTreeWidgetItem:
        """Render one node and all its descendants. Styling is driven by depth and
        node_type (not by a 2-level tool/substep split), so the tree nests without
        limit: top-level steps read boldest, deeper sub-tasks progressively muted."""
        running = node.status == "running"
        # Running rows get a spinning-ring icon (set below); others a status glyph.
        glyph = "" if running else _GLYPH.get(node.status, "·")
        indicator = f"{glyph} {node.step}".strip() if (depth == 0 and node.step) else glyph
        head = _node_head(node)
        if node.duration_ms > 0 and node.status in ("completed", "failed"):
            status_text = _fmt_ms(node.duration_ms)
        elif node.status in ("running", "waiting"):
            status_text = node.status
        else:
            status_text = ""
        item = QTreeWidgetItem([indicator, head, status_text])
        item.setForeground(0, _status_color(node.status))
        item.setForeground(1, _red() if node.status == "failed" else _depth_color(depth))
        item.setForeground(2, _red() if node.status == "failed" else _muted())
        if depth == 0:
            item.setFont(1, _semibold())

        item.setData(0, _NODE_ROLE, {
            "node_id": node.id, "stage": node.stage, "phase": node.phase,
            "agent": node.agent_name, "status": node.status, "title": node.title,
            "detail": node.detail, "duration_ms": node.duration_ms, "step": node.step,
            "thought": node.thought, "node_type": node.node_type, "raw": node.raw,
        })
        if running:
            item.setIcon(0, spinner_icon(self._busy.angle, color=Theme.BLUE))
            self._running_items.append(item)

        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)

        if node.thought:
            self._add_leaf(item, f"💭 {node.thought}", muted=True)
        # Surface the useful result/summary unless it's already in the head.
        secondary = _secondary_text(node)
        if secondary and secondary not in head:
            self._add_leaf(item, secondary, muted=True)

        for child in node.children:
            self._add_node(item, child, depth=depth + 1)

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
        raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
        try:
            self._raw_text = json.dumps(raw, ensure_ascii=False, indent=2, default=str) if raw else ""
        except (TypeError, ValueError):
            self._raw_text = str(raw)
        self._detail_title.setText(str(data.get("title") or data.get("phase") or data.get("stage") or ""))
        self._copy_raw_btn.setVisible(bool(self._raw_text) and not data.get("__summary__"))
        self._detail.setHtml(_detail_html(data))

    def _copy_raw(self) -> None:
        if self._raw_text:
            QApplication.clipboard().setText(self._raw_text)
            self._copy_raw_btn.setText("Copied ✓")
            QTimer.singleShot(1200, lambda: self._copy_raw_btn.setText("Copy raw"))


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _detail_html(data: dict) -> str:
    """Formatted (not raw-JSON) detail for the selected node. The raw event is still
    available via the Copy raw button."""
    if data.get("__summary__"):
        return f"<div style='color:{Theme.TEXT}; font-size:12px;'>{_esc(data.get('title') or '')}</div>"
    node_type = str(data.get("node_type") or "info")
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    parts: list[str] = []
    title = str(data.get("title") or data.get("phase") or data.get("stage") or "step")
    parts.append(f"<div style='color:{Theme.TEXT}; font-size:13px; font-weight:600;'>{_esc(title)}</div>")

    chips = [("type", node_type)]
    for key in ("phase", "stage", "agent"):
        if data.get(key):
            chips.append((key, str(data[key])))
    if data.get("step"):
        chips.append(("step", str(data["step"])))
    chips.append(("status", str(data.get("status") or "?")))
    if data.get("duration_ms"):
        chips.append(("", f"{float(data['duration_ms']):.0f} ms"))
    chip_html = " ".join(
        f"<span style='color:{Theme.MUTED};'>{(_esc(k) + ': ') if k else ''}{_esc(v)}</span>"
        for k, v in chips
    )
    parts.append(f"<div style='font-size:11px; margin:4px 0 8px;'>{chip_html}</div>")

    if node_type == "sql":
        facts = []
        if raw.get("row_count") not in (None, ""):
            facts.append(f"{_esc(raw['row_count'])} rows")
        if raw.get("database"):
            facts.append(f"db={_esc(raw['database'])}")
        if facts:
            parts.append(f"<div style='color:{Theme.TEXT_2}; font-size:11px; margin-bottom:6px;'>"
                         f"{' · '.join(facts)}</div>")
        sql = str(raw.get("sql") or data.get("detail") or "").strip()
        if sql:
            parts.append(_code_block(sql))
    else:
        if data.get("thought"):
            parts.append(f"<div style='color:{Theme.TEXT_2}; font-style:italic; margin-bottom:6px;'>"
                         f"💭 {_esc(data['thought'])}</div>")
        detail = str(data.get("detail") or "").strip()
        if detail:
            # Render a SQL-ish or multi-line detail as a code block, else as text.
            if "\n" in detail or detail.upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE")):
                parts.append(_code_block(detail))
            else:
                parts.append(f"<div style='color:{Theme.TEXT}; font-size:12px;'>{_esc(detail)}</div>")
        args = raw.get("args")
        if args:
            parts.append(f"<div style='color:{Theme.MUTED}; font-size:11px; margin-top:6px;'>args</div>")
            parts.append(_code_block(_esc(args), escaped=True))
    return "".join(parts)


def _code_block(text: str, *, escaped: bool = False) -> str:
    body = text if escaped else _esc(text)
    return (
        f"<pre style='background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};"
        f" border-radius:6px; padding:8px; font-family:Menlo,monospace; font-size:11px;"
        f" color:{Theme.TEXT}; white-space:pre-wrap;'>{body}</pre>"
    )


def _node_head(node: TraceNode) -> str:
    """Row label, used at any depth. Sub-agent nodes read 'agent · title'; others
    show a category chip in front of their phase/stage."""
    if node.agent_name:
        title = node.title or node.phase or node.stage or "step"
        return f"{node.agent_name} · {title}"
    base = node.phase or node.stage or node.title or "step"
    chip = STEP_TYPE_LABELS.get(node.node_type, "")
    if chip and node.node_type in _CHIP_TYPES:
        return f"{chip} · {base}"
    return base


def _depth_color(depth: int) -> QColor:
    """Top-level steps read brightest; deeper sub-tasks fade so the hierarchy is legible."""
    if depth <= 0:
        return _bright()
    if depth == 1:
        return QColor(Theme.TEXT_2)
    return _muted()


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
