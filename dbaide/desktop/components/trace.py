from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, QRect, QEvent
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.trace_model import (
    TraceModel,
    TraceNode,
    localized_node_head,
    localized_phase,
    localized_status,
    localized_summary_line,
)
from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator, SPINNER_SIZE, spinner_icon
from dbaide.desktop.trace_state import InlineTraceState
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
# Header action icons — match rendered svg size to setIconSize to stay crisp.
_TRACE_ACTION_ICON_SIZE = 18
_TRACE_ACTION_BTN_SIZE = 30


class InlineTrace(QFrame):
    """Collapsible, per-turn execution trace shown inline in the conversation (there
    is no side panel). A compact header carries a copy-trace action; below it the
    tool/sub-agent tree (parallel work as sibling nodes). Clicking any step opens a
    popup with its full detail — the tree itself stays lean and scannable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inlineTrace")
        self.setStyleSheet(
            f"QFrame#inlineTrace {{ background: {Theme.PANEL}; border: 1px solid {Theme.BORDER_SOFT};"
            f" border-radius: {Theme.RADIUS_LG}px; }}"
        )
        self.setMaximumHeight(340)
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel(t("trace.title"))
        title.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)
        self._copy_btn = QToolButton()
        self._copy_btn.setIcon(svg_icon("copy", color=Theme.TEXT_2, size=_TRACE_ACTION_ICON_SIZE))
        self._copy_btn.setIconSize(QSize(_TRACE_ACTION_ICON_SIZE, _TRACE_ACTION_ICON_SIZE))
        self._copy_btn.setToolTip(t("trace.copy"))
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setFixedSize(_TRACE_ACTION_BTN_SIZE, _TRACE_ACTION_BTN_SIZE)
        self._copy_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 7px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        )
        self._copy_btn.clicked.connect(self._copy_all)
        header.addWidget(self._copy_btn)
        layout.addLayout(header)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderHidden(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setStretchLastSection(False)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Word-wrap OFF on purpose: rows stay one line and elide at the panel edge;
        # the full text of any step is one click away in the detail popup.
        self._tree.setWordWrap(False)
        self._tree.setUniformRowHeights(False)
        self._tree.setIndentation(14)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setFont(QFont("Inter", 11))
        self._tree.setIconSize(QSize(SPINNER_SIZE, SPINNER_SIZE))
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        self._tree.itemClicked.connect(self._on_click)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemCollapsed.connect(self._on_item_collapsed)
        self._tree.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        layout.addWidget(self._tree, 1)

        self._state = InlineTraceState()
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

    # ── Public API ────────────────────────────────────────────────────────────

    def set_events(self, events: list[dict[str, Any]], *, live: bool = False) -> None:
        """Rebuild from a list of events. ``live=True`` leaves the model un-finalized
        (the run is still going); ``live=False`` finalizes it."""
        self._state.set_events(events, live=live)
        self._render()

    def begin_live(self) -> None:
        self._state.begin_live()
        self._render()

    def append_live_event(self, event: dict[str, Any]) -> None:
        self._state.append_live_event(event)
        if not self._render_timer.isActive():
            self._render_timer.start()  # coalesce bursts into one render per ~60ms

    def end_live(self) -> None:
        self._render_timer.stop()
        self._state.end_live()
        self._render()

    def clear_trace(self) -> None:
        self._state.clear()
        self._running_items = []
        self._busy.stop()
        self._tree.clear()

    def is_empty(self) -> bool:
        return self._state.is_empty()

    def copy_text(self) -> str:
        """Readable, structured export of this run (steps + SQL, nothing elided)."""
        from dbaide.agent.trace_model import render_trace_text
        return render_trace_text(self._state.model) if self._state.model is not None else ""

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _copy_all(self) -> None:
        text = self.copy_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._copy_btn.setIcon(svg_icon("check", color=Theme.GREEN, size=_TRACE_ACTION_ICON_SIZE))
        QTimer.singleShot(
            1200,
            lambda: self._copy_btn.setIcon(
                svg_icon("copy", color=Theme.TEXT_2, size=_TRACE_ACTION_ICON_SIZE)
            ),
        )

    def _render(self) -> None:
        self._tree.clear()
        self._running_items = []
        model = self._state.model
        if model is None:
            self._busy.stop()
            return

        summary = QTreeWidgetItem(["", localized_summary_line(model), ""])
        summary.setFont(1, _semibold())
        summary.setForeground(1, _overall_color(model.overall))
        summary.setData(0, _NODE_ROLE, {"__summary__": True, "title": localized_summary_line(model),
                                        "status": model.overall})
        self._tree.addTopLevelItem(summary)

        last: QTreeWidgetItem = summary
        for node in model.steps:
            last = self._add_node(self._tree, node, depth=0)

        self._restore_user_expansion()
        if self._state.follow_live:
            self._tree.scrollToItem(last)
        # Spin while anything is still running; stop once everything has resolved.
        if self._running_items:
            self._busy.start()
        else:
            self._busy.stop()

    def _on_spin(self) -> None:
        icon = spinner_icon(self._busy.angle, color=Theme.BLUE, size=SPINNER_SIZE)
        for item in self._running_items:
            try:
                item.setIcon(0, icon)
            except RuntimeError:
                pass  # item deleted by a concurrent re-render

    def _add_node(self, parent, node: TraceNode, *, depth: int) -> QTreeWidgetItem:
        """Render one node and all its descendants. Styling is driven by depth and
        node_type, so the tree nests without limit: top-level steps read boldest,
        deeper sub-tasks progressively muted."""
        running = node.status == "running"
        glyph = "" if running else _GLYPH.get(node.status, "·")
        indicator = f"{glyph} {node.step}".strip() if (depth == 0 and node.step) else glyph
        head = _node_head(node)
        if node.duration_ms > 0 and node.status in ("completed", "failed"):
            status_text = _fmt_ms(node.duration_ms)
        elif node.status in ("running", "waiting"):
            status_text = localized_status(node.status)
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
            item.setIcon(0, spinner_icon(self._busy.angle, color=Theme.BLUE, size=SPINNER_SIZE))
            self._running_items.append(item)

        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)

        # Top-level steps carry their headline (thought + one-line result) inline;
        # deeper sub-steps stay single-line and reveal their detail on click.
        if depth == 0:
            if node.thought:
                self._add_leaf(item, f"“{node.thought}”", muted=True)
            secondary = _secondary_text(node)
            if secondary and secondary not in head and head not in secondary:
                self._add_leaf(item, secondary, muted=True)

        for child in node.children:
            self._add_node(item, child, depth=depth + 1)

        return item

    def _add_leaf(self, parent: QTreeWidgetItem, text: str, *, muted: bool = True) -> None:
        leaf = QTreeWidgetItem(["", text[:600], ""])
        leaf.setForeground(1, _muted() if muted else _bright())
        leaf.setFirstColumnSpanned(True)
        parent.addChild(leaf)

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, _NODE_ROLE)
        if not isinstance(data, dict) or data.get("__summary__"):
            return
        self._state.selected_id = str(data.get("node_id") or "")
        show_trace_detail(self.window(), data)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        node_id = self._item_node_id(item)
        if not node_id:
            return
        self._state.expanded_node_ids.add(node_id)
        self._state.follow_live = False

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        node_id = self._item_node_id(item)
        if node_id:
            self._state.expanded_node_ids.discard(node_id)

    def _on_user_scroll(self, value: int) -> None:
        bar = self._tree.verticalScrollBar()
        if value < bar.maximum() - 8:
            self._state.follow_live = False

    def _item_node_id(self, item: QTreeWidgetItem) -> str:
        data = item.data(0, _NODE_ROLE)
        if not isinstance(data, dict):
            return ""
        return str(data.get("node_id") or "").strip()

    def _restore_user_expansion(self) -> None:
        if not self._state.expanded_node_ids:
            return
        self._tree.blockSignals(True)
        try:
            for item in self._iter_items():
                node_id = self._item_node_id(item)
                if node_id and node_id in self._state.expanded_node_ids:
                    item.setExpanded(True)
        finally:
            self._tree.blockSignals(False)

    def _iter_items(self):
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item is None:
                continue
            stack = [item]
            while stack:
                current = stack.pop()
                yield current
                for child_index in range(current.childCount()):
                    child = current.child(child_index)
                    if child is not None:
                        stack.append(child)

    @property
    def _model(self) -> TraceModel | None:
        return self._state.model

    @_model.setter
    def _model(self, value: TraceModel | None) -> None:
        self._state.model = value

    @property
    def _selected_id(self) -> str:
        return self._state.selected_id

    @_selected_id.setter
    def _selected_id(self, value: str) -> None:
        self._state.selected_id = str(value or "")


class TraceDetailPanel(QFrame):
    """Right-side slide-over panel for a single trace step (one at a time per window)."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self.setObjectName("traceDetailPanel")
        self.setStyleSheet(
            f"QFrame#traceDetailPanel {{ background: {Theme.BG};"
            f" border-left: 1px solid {Theme.BORDER_SOFT}; }}"
        )
        self._raw_text = ""
        self._anim: QPropertyAnimation | None = None
        self._build_ui()
        self.hide()
        host.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._host and self.isVisible() and event.type() == event.Type.Resize:
            self._relayout(animate=False)
        return super().eventFilter(obj, event)

    def _build_ui(self) -> None:
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 14px; font-weight: 700; background: transparent;"
        )
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        close = QToolButton()
        close.setIcon(svg_icon("x", color=Theme.TEXT_2, size=16))
        close.setIconSize(QSize(16, 16))
        close.setToolTip(t("dialog.close"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(30, 30)
        close.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: {Theme.RADIUS_MD}px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        )
        close.clicked.connect(self.close_panel)
        header.addWidget(close)
        layout.addLayout(header)

        self._body = QTextBrowser()
        self._body.setFont(QFont("Inter", 11))
        configure_readonly_text_view(self._body)
        self._body.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        layout.addWidget(self._body, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self._copy_raw = compact_button(
            t("trace.copy_raw"),
            icon=svg_icon("copy", color=Theme.TEXT_2, size=14),
        )
        self._copy_raw.clicked.connect(self._do_copy_raw)
        self._copy_raw.hide()
        row.addWidget(self._copy_raw)
        layout.addLayout(row)

    def show_detail(self, data: dict) -> None:
        title = str(data.get("title") or data.get("phase") or data.get("stage") or "step")
        self._title.setText(title)
        raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
        try:
            self._raw_text = json.dumps(raw, ensure_ascii=False, indent=2, default=str) if raw else ""
        except (TypeError, ValueError):
            self._raw_text = str(raw)
        self._body.setHtml(_detail_html(data))
        self._copy_raw.setVisible(bool(self._raw_text))
        self._relayout(animate=True)

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        w = self.width()
        h = self.height()
        x = self.x()
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(180)
        anim.setStartValue(QRect(x, 0, w, h))
        anim.setEndValue(QRect(self._host.width(), 0, w, h))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim

    def _panel_width(self) -> int:
        return min(440, max(320, int(self._host.width() * 0.36)))

    def _relayout(self, *, animate: bool) -> None:
        w = self._panel_width()
        h = self._host.height()
        target = QRect(self._host.width() - w, 0, w, h)
        if animate and not self.isVisible():
            start = QRect(self._host.width(), 0, w, h)
            self.setGeometry(start)
            self.show()
            self.raise_()
            anim = QPropertyAnimation(self, b"geometry", self)
            anim.setDuration(200)
            anim.setStartValue(start)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
            self._anim = anim
        else:
            self.setGeometry(target)
            self.show()
            self.raise_()

    def _do_copy_raw(self) -> None:
        if self._raw_text:
            QApplication.clipboard().setText(self._raw_text)
            self._copy_raw.setText("✓")
            self._copy_raw.setIcon(svg_icon("check", color=Theme.GREEN, size=14))
            QTimer.singleShot(1200, self._restore_copy_raw_button)

    def _restore_copy_raw_button(self) -> None:
        self._copy_raw.setText(_copy_raw_label())
        self._copy_raw.setIcon(svg_icon("copy", color=Theme.TEXT_2, size=14))


def show_trace_detail(host: QWidget, data: dict) -> None:
    """Show (or update) the single slide-over trace detail panel for this window."""
    window = host.window()
    panel = getattr(window, "_trace_detail_panel", None)
    if panel is None:
        panel = TraceDetailPanel(window)
        window._trace_detail_panel = panel
    panel.show_detail(data)


# Backwards-compatible alias for tests / imports.
TraceDetailDialog = TraceDetailPanel


def _copy_raw_label() -> str:
    from dbaide.i18n import t
    return t("trace.copy_raw")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _detail_html(data: dict) -> str:
    """Formatted (not raw-JSON) detail for the selected node. The raw event is still
    available via the Copy raw button."""
    if data.get("__summary__"):
        return f"<div style='color:{Theme.TEXT}; font-size:12px;'>{_esc(data.get('title') or '')}</div>"
    from dbaide.i18n import t
    node_type = str(data.get("node_type") or "info")
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    parts: list[str] = []
    title = _detail_title(data)
    parts.append(f"<div style='color:{Theme.TEXT}; font-size:13px; font-weight:600;'>{_esc(title)}</div>")

    # Keep the chip row lean: skip phase/stage/agent values that just echo the
    # title (or each other), so it carries information rather than noise.
    title_l = title.lower()
    chips: list[tuple[str, str]] = []
    seen_vals = {title_l}
    for key in ("agent", "phase", "stage"):
        val = str(data.get(key) or "").strip()
        if key == "phase":
            val = localized_phase(str(data.get("stage") or ""), val)
        if val and val.lower() not in seen_vals:
            label = {
                "agent": t("trace.field.agent"),
                "phase": t("trace.field.stage"),
                "stage": t("trace.field.stage"),
            }.get(key, key)
            chips.append((label, val))
            seen_vals.add(val.lower())
    if data.get("step"):
        chips.append(("", t("trace.step", n=data["step"])))
    chips.append((t("trace.field.status"), localized_status(str(data.get("status") or "?"))))
    if data.get("duration_ms"):
        chips.append((t("trace.field.duration"), f"{float(data['duration_ms']):.0f} ms"))
    sep = f"<span style='color:{Theme.MUTED_2};'> · </span>"
    chip_html = sep.join(
        f"<span style='color:{Theme.MUTED};'>{(_esc(k) + ' ') if k else ''}"
        f"<span style='color:{Theme.TEXT_2};'>{_esc(v)}</span></span>"
        for k, v in chips
    )
    parts.append(f"<div style='font-size:11px; margin:4px 0 8px;'>{chip_html}</div>")

    if data.get("thought"):
        parts.append(_section(t("trace.field.thought"), str(data["thought"])))

    if raw.get("args"):
        parts.append(_section(t("trace.field.input"), _json_text(raw.get("args")), code=True))

    if raw.get("decision") not in (None, "", {}, []):
        parts.append(_section(t("trace.field.decision"), _json_text(raw.get("decision")), code=True))

    if node_type == "sql":
        facts = []
        if raw.get("row_count") not in (None, ""):
            facts.append(t("trace.field.rows", n=raw["row_count"]))
        if raw.get("database"):
            facts.append(f"{t('trace.field.database')}={raw['database']}")
        if facts:
            parts.append(f"<div style='color:{Theme.TEXT_2}; font-size:11px; margin-bottom:6px;'>"
                         f"{_esc(' · '.join(str(x) for x in facts))}</div>")
        sql = str(raw.get("sql") or data.get("detail") or "").strip()
        if sql:
            parts.append(_section(t("trace.field.sql"), sql, code=True))
    else:
        detail = str(data.get("detail") or "").strip()
        if detail:
            # Render a SQL-ish or multi-line detail as a code block, else as text.
            if "\n" in detail or detail.upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE")):
                parts.append(_section(t("trace.field.output"), detail, code=True))
            else:
                parts.append(_section(t("trace.field.output"), detail))

    for key, label_key in (
        ("output", "trace.field.output"),
        ("result_data", "trace.field.result_data"),
    ):
        value = raw.get(key)
        if value not in (None, "", {}, []):
            parts.append(_section(t(label_key), _json_text(value), code=True))

    question = str(raw.get("question") or "").strip()
    if question:
        parts.append(_section(t("trace.field.question"), question))
    options = raw.get("options")
    if isinstance(options, list) and options:
        parts.append(_section(t("trace.field.options"), "\n".join(f"- {x}" for x in options)))
    questions = raw.get("questions")
    if isinstance(questions, list) and questions:
        parts.append(_section(t("trace.field.question"), _json_text(questions), code=True))

    llm_call = raw.get("llm_call") if isinstance(raw.get("llm_call"), dict) else None
    llm_calls = [llm_call] if llm_call else raw.get("llm_calls")
    if isinstance(llm_calls, list) and llm_calls:
        parts.append(_llm_calls_html(llm_calls))
    if raw:
        parts.append(_section(t("trace.field.raw_event"), _json_text(raw), code=True))
    return "".join(parts)


def _detail_title(data: dict) -> str:
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    node = TraceNode(
        id=str(data.get("node_id") or "detail"),
        parent_id="",
        stage=str(data.get("stage") or ""),
        phase=str(data.get("phase") or ""),
        agent=str(raw.get("agent") or data.get("agent") or ""),
        kind=str(raw.get("kind") or ""),
        node_type=str(data.get("node_type") or "info"),
        status=str(data.get("status") or ""),
        title=str(data.get("title") or ""),
        detail=str(data.get("detail") or ""),
        duration_ms=float(data.get("duration_ms") or 0.0),
        step=int(data.get("step") or 0),
        thought=str(data.get("thought") or ""),
        raw=dict(raw),
    )
    return localized_node_head(node)


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _section(title: str, body: str, *, code: bool = False) -> str:
    if not str(body or "").strip():
        return ""
    head = f"<div style='color:{Theme.MUTED}; font-size:11px; font-weight:600; margin-top:9px;'>{_esc(title)}</div>"
    if code:
        return head + _code_block(body)
    return head + f"<div style='color:{Theme.TEXT}; font-size:12px; white-space:pre-wrap;'>{_esc(body)}</div>"


def _llm_calls_html(calls: list[dict]) -> str:
    from dbaide.i18n import t
    parts = [f"<div style='color:{Theme.MUTED}; font-size:11px; font-weight:600; margin-top:9px;'>"
             f"{_esc(t('trace.field.llm_calls'))} ({len(calls)})</div>"]
    for idx, call in enumerate(calls, 1):
        if not isinstance(call, dict):
            continue
        meta = " · ".join(str(x) for x in (call.get("stage"), call.get("method"), f"{call.get('ms')}ms" if call.get("ms") else "") if x)
        parts.append(f"<div style='color:{Theme.TEXT_2}; font-size:11px; margin-top:7px;'>#{idx} {_esc(meta)}</div>")
        for msg in call.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "message")
            parts.append(_section(f"{t('trace.field.prompt')} · {role}", str(msg.get("content") or ""), code=True))
        parts.append(_section(t("trace.field.response"), str(call.get("response") or ""), code=True))
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
    return localized_node_head(node)


def _depth_color(depth: int) -> QColor:
    """Top-level steps read brightest; deeper sub-tasks fade so the hierarchy is legible."""
    if depth <= 0:
        return _bright()
    if depth == 1:
        return QColor(Theme.TEXT_2)
    return _muted()


def _secondary_text(node: TraceNode) -> str:
    """The useful one-line summary for a tool step: its result detail, or a
    non-boilerplate title. Returns '' when there's nothing worth a second line."""
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
