from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from dbaide.agent.trace_model import TraceModel, TraceStep
from dbaide.desktop.theme import Theme

# Compact status glyphs so you can see state at a glance.
_GLYPH = {
    "running": "▶",
    "completed": "✓",
    "failed": "✗",
    "waiting": "⏸",
    "info": "·",
    "idle": "·",
    "done": "✓",
}


class TracePanel(QTreeWidget):
    """Renders the agent trace from a TraceModel: a live summary row on top, then
    one row per step (phase + title + status/duration) with nested sub-agent activity.
    """

    event_selected = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["", "Step", "Status"])
        self.header().setStretchLastSection(True)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setFont(QFont("Menlo", 10))
        self.itemClicked.connect(self._on_click)
        self._model: TraceModel | None = None
        self._live = False

    # ── Public API (preserved for callers) ───────────────────────────────────

    def load_events(self, events: list[dict[str, Any]]) -> None:
        model = TraceModel()
        for event in events or []:
            model.ingest(event)
        if model.overall == "running":
            model.overall = "done"
        self._model = model
        self._live = False
        self._render()

    def begin_live(self) -> None:
        self._model = TraceModel()
        self._live = True
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
        if self._model is not None and self._model.overall == "running":
            self._model.overall = "done"
        self._live = False
        self._render()

    def clear_trace(self) -> None:
        self._model = None
        self._live = False
        self.clear()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        self.clear()
        model = self._model
        if model is None:
            return

        summary = QTreeWidgetItem(["", model.summary_line(), ""])
        summary.setFont(1, _bold())
        summary.setForeground(1, _overall_color(model.overall))
        summary.setData(0, Qt.ItemDataRole.UserRole, {
            "stage": "summary", "title": model.summary_line(),
            "status": model.overall, "kind": "info",
        })
        self.addTopLevelItem(summary)

        last_item: QTreeWidgetItem | None = summary
        for step in model.steps:
            item = self._render_step(step)
            self.addTopLevelItem(item)
            last_item = item

        if last_item is not None:
            self.scrollToItem(last_item)

    def _render_step(self, step: TraceStep) -> QTreeWidgetItem:
        glyph = _GLYPH.get(step.status, "·")
        head = step.phase or step.stage
        if step.title and step.title not in head:
            head = f"{head} — {step.title}"
        status_text = f"{step.duration_ms:.0f} ms" if (step.status == "completed" and step.duration_ms > 0) else step.status
        prefix = f"{glyph} {step.step}" if step.step else glyph
        item = QTreeWidgetItem([prefix, head, status_text])
        color = _status_color(step.status)
        item.setForeground(1, color)
        item.setForeground(2, color)
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "stage": step.stage, "title": step.title or step.phase,
            "status": step.status, "kind": "tool", "detail": step.detail,
            "duration_ms": step.duration_ms, "phase": step.phase, "step": step.step,
        })

        if step.thought:
            thought = QTreeWidgetItem(["", f"💭 {step.thought[:300]}", ""])
            thought.setForeground(1, _muted())
            item.addChild(thought)

        for sub in step.substeps:
            line = f"{sub.label}: {sub.title}" if sub.label else sub.title
            child = QTreeWidgetItem(["", line[:400], _GLYPH.get(sub.status, "")])
            child.setForeground(1, _agent_color(sub.status))
            if sub.detail:
                child.addChild(QTreeWidgetItem(["", sub.detail[:400], ""]))
            item.addChild(child)

        if step.detail and step.status != "running":
            item.addChild(QTreeWidgetItem(["", step.detail[:400], ""]))

        item.setExpanded(True)
        return item

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self.event_selected.emit(data)


def _bold() -> QFont:
    f = QFont("Menlo", 10)
    f.setBold(True)
    return f


def _overall_color(overall: str) -> QColor:
    return {
        "done": _green(),
        "failed": _red(),
        "running": _blue(),
    }.get(overall, _blue())


def _status_color(status: str) -> QColor:
    return {
        "completed": _green(),
        "failed": _red(),
        "waiting": _yellow(),
        "running": _blue(),
    }.get(status, _blue())


def _agent_color(status: str) -> QColor:
    if status == "failed":
        return _red()
    if status == "completed":
        return _green()
    return _muted()


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
