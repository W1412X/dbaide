from __future__ import annotations

import json
import weakref
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dbaide.agent.trace_model import (
    TraceModel,
    TraceNode,
    TraceTimelineEntry,
    build_trace_timeline,
    localized_node_head,
    localized_phase,
    localized_status,
    localized_summary_line,
)
from dbaide.desktop.components.base import ghost_action_button, clear_layout_widgets, discard_widget
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_readonly_text_view
from dbaide.desktop.components.spinner import BusyAnimator, SPINNER_SIZE, spinner_pixmap
from dbaide.desktop.trace.helpers import (
    alive_widget as _alive_widget,
    clamp_drawer_geometry,
    follow_at_bottom as _follow_at_bottom,
    is_descendant as _is_descendant,
    timeline_structure_fingerprint,
)
from dbaide.desktop.trace.session import TraceViewState
from dbaide.desktop.trace_state import InlineTraceState
from dbaide.desktop.theme import Theme

# Header action icons — match rendered svg size to setIconSize to stay crisp.
_TRACE_ACTION_ICON_SIZE = 18
_TRACE_ACTION_BTN_SIZE = 30
_TIMELINE_ICON_W = 28
_TIMELINE_ICON_H = 44
_CARD_RADIUS = 8
_DRAWER_W = 520
_DRAWER_DETAIL_H = 280


def close_trace_overlays(host: QWidget) -> None:
    from dbaide.desktop.trace.overlay import close_trace_overlays as _close

    _close(host)


def close_trace_overlays_for(host: QWidget) -> None:
    from dbaide.desktop.trace.overlay import close_trace_overlays_for as _close_for

    _close_for(host)


class InlineTrace(QFrame):
    """Timeline view for one run's trace.

    This widget is presentation-only. It renders a display-specific timeline model
    derived from the full trace, while preserving the original raw event payloads for
    export and step inspection.
    """

    def __init__(self, parent=None, *, show_header: bool = True, detail_handler=None) -> None:
        super().__init__(parent)
        self._show_header = bool(show_header)
        self._detail_handler = detail_handler
        self.setObjectName("inlineTrace")
        self.setStyleSheet(
            f"QFrame#inlineTrace {{ background: {'transparent' if not self._show_header else Theme.PANEL}; "
            f"border: {'none' if not self._show_header else '1px solid ' + Theme.BORDER_SOFT}; "
            f"border-radius: {Theme.RADIUS_MD}px; }}"
        )
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0 if not self._show_header else 10, 0 if not self._show_header else 8, 0 if not self._show_header else 10, 0 if not self._show_header else 10)
        layout.setSpacing(8)

        header_wrap = QWidget(self)
        header = QHBoxLayout(header_wrap)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel(t("trace.title"), header_wrap)
        title.setStyleSheet(f"color: {Theme.MUTED}; font-size: 10px; font-weight: 600; background: transparent;")
        header.addWidget(title)
        self._summary = QLabel("", header_wrap)
        self._summary.setStyleSheet(f"color: {Theme.MUTED_2}; font-size: 10px; background: transparent;")
        header.addWidget(self._summary)
        header.addStretch(1)
        self._copy_btn = QToolButton(header_wrap)
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
        header_wrap.setVisible(self._show_header)
        layout.addWidget(header_wrap)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        self._body = QWidget(self._scroll)
        self._body.setStyleSheet("background: transparent;")
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 2, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._scroll.setWidget(self._body)
        layout.addWidget(self._scroll, 1)

        self._state = InlineTraceState()
        self._cards: list[_TraceStepCard] = []
        self._running_glyphs: list[_TraceTimelineGlyph] = []
        self._render_epoch = 0
        self._last_struct_fp: tuple[tuple, ...] = ()
        self._tree = _TraceTreeCompat(self)
        self._busy = BusyAnimator(self._on_spin, parent=self)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._render)

    # ── Public API ────────────────────────────────────────────────────────────

    def bind_state(self, state: TraceViewState) -> None:
        self._state = state

    def set_events(self, events: list[dict[str, Any]], *, live: bool = False) -> None:
        """Rebuild from a list of events. ``live=True`` leaves the model un-finalized
        (the run is still going); ``live=False`` finalizes it."""
        self._state.set_events(events, live=live)
        if live:
            self._schedule_render()
        else:
            self._render_timer.stop()
            self._render()

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def begin_live(self) -> None:
        self._state.begin_live()
        self._render()

    def append_live_event(self, event: dict[str, Any]) -> None:
        self._state.append_live_event(event)
        self._schedule_render()

    def end_live(self) -> None:
        self._render_timer.stop()
        self._state.end_live()
        self._render()

    def clear_trace(self) -> None:
        self._state.clear()
        self._last_struct_fp = ()
        self._clear_cards()
        self._running_glyphs = []
        self._busy.stop()
        self._summary.setText("")

    def is_empty(self) -> bool:
        return self._state.is_empty()

    def copy_text(self) -> str:
        """Readable, structured export of this run (steps + SQL, nothing elided)."""
        from dbaide.agent.trace_model import render_trace_text
        return render_trace_text(self._state.model) if self._state.model is not None else ""

    def summary_text(self) -> str:
        return self._summary.text()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _copy_all(self) -> None:
        text = self.copy_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._copy_btn.setIcon(svg_icon("check", color=Theme.GREEN, size=_TRACE_ACTION_ICON_SIZE))

        def _restore_icon() -> None:
            try:
                from PyQt6 import sip
                if not sip.isdeleted(self._copy_btn):
                    self._copy_btn.setIcon(
                        svg_icon("copy", color=Theme.TEXT_2, size=_TRACE_ACTION_ICON_SIZE)
                    )
            except RuntimeError:
                pass

        QTimer.singleShot(1200, _restore_icon)

    def _render(self) -> None:
        self.setUpdatesEnabled(False)
        last_widget: QWidget | None = None
        # Preserve the user's scroll position across a full rebuild. Some live
        # transitions (a step gaining its first sub-step, a subtitle appearing) can't
        # be applied incrementally and fall back to _full_rebuild, which would otherwise
        # snap the view to the top. When following the tail we re-scroll to the newest
        # card below instead, so only capture when not following.
        prev_scroll = None if self._state.follow_live else self._scroll.verticalScrollBar().value()
        try:
            model = self._state.model
            if model is None:
                self._last_struct_fp = ()
                self._clear_cards()
                self._running_glyphs = []
                self._summary.setText("")
                self._busy.stop()
                return

            self._summary.setText(localized_summary_line(model))
            timeline = build_trace_timeline(model)
            struct_fp = timeline_structure_fingerprint(timeline)
            total = len(timeline)
            prefix = struct_fp[: len(self._cards)]
            can_sync_prefix = (
                len(self._cards) > 0
                and len(self._cards) <= total
                and prefix == self._last_struct_fp[: len(self._cards)]
                and self._card_ids_match(timeline, len(self._cards))
            )
            if can_sync_prefix:
                self._running_glyphs = []
                if self._sync_existing_cards(timeline[: len(self._cards)]):
                    if total > len(self._cards):
                        last_widget = self._append_cards(timeline, start=len(self._cards))
                    else:
                        last_widget = self._cards[-1] if self._cards else None
                    self._last_struct_fp = struct_fp
                else:
                    last_widget = self._full_rebuild(timeline, struct_fp)
            elif struct_fp == self._last_struct_fp and len(self._cards) == total:
                self._running_glyphs = []
                if self._sync_existing_cards(timeline):
                    last_widget = self._cards[-1] if self._cards else None
                else:
                    last_widget = self._full_rebuild(timeline, struct_fp)
            else:
                last_widget = self._full_rebuild(timeline, struct_fp)

            if self._running_glyphs:
                self._busy.start()
            else:
                self._busy.stop()
        finally:
            self.setUpdatesEnabled(True)
        if self._state.follow_live and last_widget is not None:
            QTimer.singleShot(0, lambda w=last_widget: self._scroll_to_widget(w))
        elif prev_scroll is not None:
            # Restore the pre-render offset (clamped to the new content height) so a
            # full rebuild doesn't lose the reader's place. Block signals so this
            # programmatic scroll doesn't flip follow_live via _on_user_scroll.
            bar = self._scroll.verticalScrollBar()
            bar.blockSignals(True)
            try:
                bar.setValue(min(prev_scroll, bar.maximum()))
            finally:
                bar.blockSignals(False)

    def _card_ids_match(self, timeline: list[TraceTimelineEntry], count: int) -> bool:
        if count > len(self._cards) or count > len(timeline):
            return False
        for index in range(count):
            if self._cards[index]._entry.node_id != timeline[index].node_id:
                return False
            if self._cards[index]._depth != timeline[index].depth:
                return False
        return True

    def _full_rebuild(
        self,
        timeline: list[TraceTimelineEntry],
        struct_fp: tuple[tuple, ...],
    ) -> QWidget | None:
        self._render_epoch += 1
        self._last_struct_fp = struct_fp
        self._clear_cards()
        self._running_glyphs = []
        return self._append_cards(timeline, start=0)

    def _append_cards(
        self,
        timeline: list[TraceTimelineEntry],
        *,
        start: int,
    ) -> QWidget | None:
        total = len(timeline)
        last_widget: QWidget | None = None
        for index in range(start, total):
            entry = timeline[index]
            card = _TraceStepCard(
                entry,
                is_first=index == 0,
                is_last=index == total - 1,
                depth=entry.depth,
                expanded_ids=self._state.expanded_node_ids,
                on_toggle=self._set_expanded,
                on_open=self._open_detail,
                render_epoch=lambda: self._render_epoch,
                running_glyphs=self._running_glyphs,
            )
            self._body_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
            self._cards.append(card)
            last_widget = card
        return last_widget

    def _sync_existing_cards(self, timeline: list[TraceTimelineEntry]) -> bool:
        self._running_glyphs = []
        total = len(timeline)
        for index, (card, entry) in enumerate(zip(self._cards, timeline, strict=False)):
            if not card.apply_entry(
                entry,
                depth=entry.depth,
                is_first=index == 0,
                is_last=index == total - 1,
                running_glyphs=self._running_glyphs,
            ):
                return False
        return True

    def _on_spin(self) -> None:
        for glyph in self._running_glyphs:
            try:
                glyph.set_spin_angle(self._busy.angle)
            except RuntimeError:
                pass

    def _on_user_scroll(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        self._state.follow_live = _follow_at_bottom(value, bar.maximum())

    def _set_expanded(self, node_id: str, expanded: bool) -> None:
        if not node_id:
            return
        if expanded:
            self._state.expanded_node_ids.add(node_id)
            self._state.follow_live = False
        else:
            self._state.expanded_node_ids.discard(node_id)

    def _open_detail(self, data: dict[str, Any]) -> None:
        self._state.selected_id = str(data.get("node_id") or "")
        if callable(self._detail_handler):
            self._detail_handler(data)
            return
        show_trace_detail(self.window(), data)

    def _clear_cards(self) -> None:
        """Remove timeline cards without orphaning them as top-level windows."""
        self._cards = []
        clear_layout_widgets(self._body_layout)

    def _scroll_to_widget(self, widget: QWidget | None) -> None:
        # Deferred via QTimer.singleShot(0, …): by the time it fires a later render may
        # have rebuilt (deleted) the target card, or this whole trace may have been torn
        # down. Touching a deleted C++ object raises RuntimeError — guard both.
        if widget is None or sip.isdeleted(widget) or sip.isdeleted(self):
            return
        try:
            bar = self._scroll.verticalScrollBar()
            bar.blockSignals(True)
            try:
                self._scroll.ensureWidgetVisible(widget, 0, 16)
            finally:
                bar.blockSignals(False)
        except RuntimeError:
            pass



class _TraceStepCard(QFrame):
    def __init__(
        self,
        entry: TraceTimelineEntry,
        *,
        is_first: bool,
        is_last: bool,
        depth: int,
        expanded_ids: set[str],
        on_toggle,
        on_open,
        render_epoch=None,
        running_glyphs: list["_TraceTimelineGlyph"],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._data = _entry_payload(entry)
        self._on_toggle = on_toggle
        self._on_open = on_open
        self._render_epoch = render_epoch
        self._depth = depth
        self._expanded_ids = expanded_ids
        self._running_glyphs = running_glyphs
        self._is_expanded = entry.node_id in expanded_ids
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 6 if depth <= 0 else 4)
        row.setSpacing(6 if depth <= 0 else 5)

        marker = _TraceTimelineGlyph(entry.status, is_first=is_first, is_last=is_last, compact=depth > 0, parent=self)
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        if entry.status == "running":
            running_glyphs.append(marker)
        self._marker = marker

        self._card = _ClickableTraceCard(
            lambda: self._on_open(self._data),
            render_epoch=render_epoch,
            parent=self,
        )
        self._card.setObjectName("traceTimelineCard")
        pad_x = 8 if depth <= 0 else 7
        pad_y = 6 if depth <= 0 else 5
        radius = 7 if depth <= 0 else 6
        panel = Theme.SURFACE if depth <= 0 else Theme.PANEL
        hover = Theme.PANEL_2 if depth <= 0 else Theme.PANEL_3
        self._card.setStyleSheet(
            f"QFrame#traceTimelineCard {{ background: {panel}; border: none; border-radius: {radius}px; }}"
            f"QFrame#traceTimelineCard:hover {{ background: {hover}; }}"
        )
        row.addWidget(self._card, 1)

        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        title = QLabel(entry.title, self._card)
        title.setWordWrap(True)
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {'12px' if depth <= 0 else '11px'}; font-weight: 700; background: transparent;"
        )
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top.addWidget(title, 1)
        self._title_label = title
        meta = QLabel(_card_meta(entry), self._card)
        meta.setStyleSheet(f"color: {_meta_color(entry.status)}; font-size: 9px; font-weight: 600; background: transparent;")
        meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top.addWidget(meta, 0, Qt.AlignmentFlag.AlignTop)
        self._meta_label = meta
        layout.addLayout(top)

        subtitle_text = _card_subtitle(entry)
        self._subtitle_label: QLabel | None = None
        if subtitle_text:
            subtitle = QLabel(subtitle_text, self._card)
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet(
                f"color: {Theme.TEXT_2 if depth <= 0 else Theme.MUTED}; font-size: {'10px' if depth <= 0 else '9px'}; "
                "background: transparent;"
            )
            subtitle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(subtitle)
            self._subtitle_label = subtitle

        self._details = QWidget(self._card)
        self._details.setStyleSheet("background: transparent;")
        self._details_layout = QVBoxLayout(self._details)
        self._details_layout.setContentsMargins(0, 1, 0, 0)
        self._details_layout.setSpacing(4)
        self._child_box: QWidget | None = None
        self._child_layout: QVBoxLayout | None = None
        self._build_details()
        layout.addWidget(self._details)

        self._expandable = self._details_layout.count() > 0
        if self._expandable:
            footer = QHBoxLayout()
            footer.setContentsMargins(0, 0, 0, 0)
            footer.setSpacing(4)
            self._toggle = QToolButton(self._card)
            self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self._toggle.setAutoRaise(True)
            self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._toggle.setStyleSheet(
                f"QToolButton {{ color: {Theme.MUTED}; background: transparent; border: none; padding: 0; font-size: 10px; font-weight: 500; }}"
                f"QToolButton:hover {{ color: {Theme.TEXT_2}; }}"
            )
            self._toggle.clicked.connect(self._toggle_details)
            footer.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addLayout(footer)
            self._sync_toggle()
        else:
            self._details.hide()

        if not self._is_expanded or not self._expandable:
            self._details.hide()
        self._sync_height_constraints()

    def trace_data(self) -> dict[str, Any]:
        return dict(self._data)

    def apply_entry(
        self,
        entry: TraceTimelineEntry,
        *,
        depth: int,
        is_first: bool,
        is_last: bool,
        running_glyphs: list["_TraceTimelineGlyph"],
    ) -> bool:
        if depth != self._depth:
            return False
        child_sig = tuple((c.node_id, c.status, len(c.children)) for c in entry.children)
        old_child_sig = tuple((c.node_id, c.status, len(c.children)) for c in self._entry.children)
        expanded = entry.node_id in self._expanded_ids
        self._entry = entry
        self._data = _entry_payload(entry)
        self._title_label.setText(entry.title)
        self._meta_label.setText(_card_meta(entry))
        self._meta_label.setStyleSheet(
            f"color: {_meta_color(entry.status)}; font-size: 9px; font-weight: 600; background: transparent;"
        )
        subtitle_text = _card_subtitle(entry)
        if self._subtitle_label is not None:
            self._subtitle_label.setText(subtitle_text)
            self._subtitle_label.setVisible(bool(subtitle_text))
        elif subtitle_text:
            return False
        self._marker.set_status(entry.status, is_first=is_first, is_last=is_last)
        if entry.status == "running":
            running_glyphs.append(self._marker)
        if expanded != self._is_expanded:
            self._is_expanded = expanded
            if self._expandable:
                self._details.setVisible(self._is_expanded)
                self._sync_toggle()
        if child_sig != old_child_sig and not self._sync_child_cards(entry, running_glyphs):
            return False
        self._sync_height_constraints()
        return True

    def _sync_child_cards(
        self,
        entry: TraceTimelineEntry,
        running_glyphs: list["_TraceTimelineGlyph"],
    ) -> bool:
        if not entry.children:
            if self._child_box is not None:
                self._child_box.hide()
            return True
        if self._child_layout is None or self._child_box is None:
            return False
        self._child_box.show()
        while self._child_layout.count():
            item = self._child_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                discard_widget(widget)
        total = len(entry.children)
        for index, child in enumerate(entry.children):
            self._child_layout.addWidget(_TraceStepCard(
                child,
                is_first=index == 0,
                is_last=index == total - 1,
                depth=self._depth + 1,
                expanded_ids=self._expanded_ids,
                on_toggle=self._on_toggle,
                on_open=self._on_open,
                render_epoch=self._render_epoch,
                running_glyphs=running_glyphs,
                parent=self._child_box,
            ))
        return True

    def child_cards(self) -> list["_TraceStepCard"]:
        out: list[_TraceStepCard] = []
        for index in range(self._details_layout.count()):
            item = self._details_layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, _TraceStepCard):
                out.append(widget)
            elif isinstance(widget, QWidget):
                out.extend(widget.findChildren(_TraceStepCard, options=Qt.FindChildOption.FindDirectChildrenOnly))
        return out

    def is_expanded(self) -> bool:
        return self._is_expanded

    def _toggle_details(self) -> None:
        self._is_expanded = not self._is_expanded
        self._details.setVisible(self._is_expanded)
        self._on_toggle(self._entry.node_id, self._is_expanded)
        self._sync_toggle()
        self._sync_height_constraints()

    def _sync_toggle(self) -> None:
        from dbaide.i18n import t

        arrow = "▾" if self._is_expanded else "▸"
        self._toggle.setText(f"{arrow} {t('trace.hide_details' if self._is_expanded else 'trace.show_details')}")

    def _build_details(self) -> None:
        for title, body, code in _inline_sections(self._data):
            section = _trace_preview_section(title, body, code=code, parent=self._details)
            if section is not None:
                self._details_layout.addWidget(section)
        if self._entry.children:
            from dbaide.i18n import t

            subhead = QLabel(t("trace.substeps"), self._details)
            subhead.setStyleSheet(f"color: {Theme.MUTED}; font-size: 9px; font-weight: 600; background: transparent;")
            self._details_layout.addWidget(subhead)
            child_box = QWidget(self._details)
            child_box.setStyleSheet("background: transparent;")
            child_layout = QVBoxLayout(child_box)
            child_layout.setContentsMargins(0, 0, 0, 0)
            child_layout.setSpacing(4)
            self._child_box = child_box
            self._child_layout = child_layout
            total = len(self._entry.children)
            for index, child in enumerate(self._entry.children):
                child_layout.addWidget(_TraceStepCard(
                    child,
                    is_first=index == 0,
                    is_last=index == total - 1,
                    depth=self._depth + 1,
                    expanded_ids=self._expanded_ids,
                    on_toggle=self._on_toggle,
                    on_open=self._on_open,
                    render_epoch=self._render_epoch,
                    running_glyphs=self._running_glyphs,
                    parent=child_box,
                ))
            self._details_layout.addWidget(child_box)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height_constraints()

    def _sync_height_constraints(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        target = max(layout.sizeHint().height(), self.minimumSizeHint().height())
        if self.height() != target or self.minimumHeight() != target or self.maximumHeight() != target:
            self.setFixedHeight(target)
        base = _TIMELINE_ICON_H if self._depth <= 0 else 40
        marker_h = max(base, target)
        if self._marker.height() != marker_h:
            self._marker.setFixedHeight(marker_h)


class _ClickableTraceCard(QFrame):
    def __init__(self, on_open, *, render_epoch=None, parent=None) -> None:
        super().__init__(parent)
        self._on_open = on_open
        self._render_epoch = render_epoch or (lambda: 0)
        self._pressed = False
        self._press_epoch: int | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = event.button() == Qt.MouseButton.LeftButton
        self._press_epoch = self._render_epoch() if self._pressed else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        same_epoch = self._press_epoch is not None and self._press_epoch == self._render_epoch()
        should_open = (
            self._pressed
            and same_epoch
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
            and callable(self._on_open)
        )
        self._pressed = False
        self._press_epoch = None
        if should_open:
            self._on_open()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TraceTimelineGlyph(QWidget):
    def __init__(self, status: str, *, is_first: bool, is_last: bool, compact: bool, parent=None) -> None:
        super().__init__(parent)
        self._status = str(status or "")
        self._is_first = bool(is_first)
        self._is_last = bool(is_last)
        self._compact = bool(compact)
        self._angle = 0.0
        self.setFixedWidth(_TIMELINE_ICON_W if not compact else 28)
        self.setMinimumHeight(_TIMELINE_ICON_H if not compact else 40)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)

    def set_spin_angle(self, angle: float) -> None:
        self._angle = float(angle or 0.0)
        self.update()

    def set_status(self, status: str, *, is_first: bool, is_last: bool) -> None:
        changed = (
            str(status or "") != self._status
            or bool(is_first) != self._is_first
            or bool(is_last) != self._is_last
        )
        self._status = str(status or "")
        self._is_first = bool(is_first)
        self._is_last = bool(is_last)
        if changed:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = int(self.width() / 2)
        cy = 18 if not self._compact else 16
        radius = 10 if not self._compact else 8
        line_pen = QPen(QColor(Theme.BORDER), 1)
        painter.setPen(line_pen)
        top_y = 0 if not self._is_first else max(0, cy - radius)
        bottom_y = self.height() if not self._is_last else min(self.height(), cy + radius)
        if top_y < cy - radius:
            painter.drawLine(x, top_y, x, cy - radius)
        if bottom_y > cy + radius:
            painter.drawLine(x, cy + radius, x, bottom_y)
        color = _status_color(self._status)
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(QColor(Theme.BG))
        painter.drawEllipse(x - radius, cy - radius, radius * 2, radius * 2)
        if self._status == "running":
            px = spinner_pixmap(self._angle, color=Theme.BLUE, size=12 if not self._compact else 10)
            painter.drawPixmap(x - int(px.width() / max(1.0, px.devicePixelRatioF()) / 2), cy - int(px.height() / max(1.0, px.devicePixelRatioF()) / 2), px)
            return
        glyph = _status_glyph(self._status)
        if glyph:
            painter.setPen(QPen(color))
            font = QFont("Inter", 9 if not self._compact else 8)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(QRect(x - radius, cy - radius, radius * 2, radius * 2), Qt.AlignmentFlag.AlignCenter, glyph)


def _entry_payload(entry: TraceTimelineEntry) -> dict[str, Any]:
    return {
        "node_id": entry.node_id,
        "stage": entry.stage,
        "phase": entry.phase,
        "agent": entry.agent,
        "status": entry.status,
        "title": entry.title,
        "detail": entry.detail,
        "duration_ms": entry.duration_ms,
        "step": entry.step,
        "thought": entry.thought,
        "node_type": entry.node_type,
        "raw": entry.raw,
    }


def _card_meta(entry: TraceTimelineEntry) -> str:
    if entry.duration_ms > 0 and entry.status in ("completed", "failed"):
        return _fmt_ms(entry.duration_ms)
    status = localized_status(entry.status)
    if status:
        return status
    if entry.children:
        from dbaide.i18n import t
        return t("trace.children", n=len(entry.children))
    return ""


def _card_subtitle(entry: TraceTimelineEntry) -> str:
    summary = " ".join(str(entry.summary or "").split()).strip()
    if summary:
        return summary
    if entry.thought:
        return entry.thought
    if entry.children:
        from dbaide.i18n import t
        agents: list[str] = []
        _collect_agents(entry.children, agents)
        head = t("trace.children", n=len(entry.children))
        if agents:
            return f"{head} · {', '.join(agents[:3])}"
        return head
    return ""


def _collect_agents(children: list[TraceTimelineEntry], out: list[str]) -> None:
    for child in children:
        if child.agent and child.agent not in out:
            out.append(child.agent)
        _collect_agents(child.children, out)


def _inline_sections(data: dict[str, Any]) -> list[tuple[str, str, bool]]:
    from dbaide.i18n import t

    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    sections: list[tuple[str, str, bool]] = []
    thought = str(data.get("thought") or "").strip()
    if thought:
        sections.append((t("trace.field.thought"), thought, False))
    args = raw.get("args")
    if args not in (None, "", {}, []):
        sections.append((t("trace.field.input"), _json_text(args), True))
    sql = str(raw.get("sql") or "").strip()
    if sql:
        sections.append((t("trace.field.sql"), sql, True))
    output = raw.get("output")
    if output not in (None, "", {}, []):
        sections.append((t("trace.field.output"), _json_text(output), isinstance(output, (dict, list, str))))
    elif str(data.get("detail") or "").strip():
        detail = str(data.get("detail") or "").strip()
        sections.append((t("trace.field.output"), detail, "\n" in detail or detail.upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE"))))
    result_data = raw.get("result_data")
    if result_data not in (None, "", {}, []):
        sections.append((t("trace.field.result_data"), _json_text(result_data), True))
    question = str(raw.get("question") or "").strip()
    if question:
        sections.append((t("trace.field.question"), question, False))
    options = raw.get("options")
    if isinstance(options, list) and options:
        sections.append((t("trace.field.options"), "\n".join(f"- {item}" for item in options), False))
    return sections


def _trace_preview_section(title: str, body: str, *, code: bool, parent: QWidget | None = None) -> QWidget | None:
    text = str(body or "").strip()
    if not text:
        return None
    wrap = QWidget(parent)
    wrap.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    head = QLabel(title, wrap)
    head.setStyleSheet(f"color: {Theme.MUTED}; font-size: 9px; font-weight: 600; background: transparent;")
    layout.addWidget(head)
    block = QLabel(_truncate_preview(text, max_lines=8 if code else 5, max_chars=1200 if code else 420), wrap)
    block.setTextFormat(Qt.TextFormat.PlainText)
    block.setWordWrap(True)
    block.setStyleSheet(
        f"color: {Theme.TEXT if code else Theme.TEXT_2}; "
        f"background: transparent; border: none; padding: 0; "
        f"font-family: {'Menlo, monospace' if code else 'Inter'}; font-size: {'10px' if code else '10px'};"
    )
    layout.addWidget(block)
    return wrap


def _truncate_preview(text: str, *, max_lines: int, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + "\n..."
    lines = raw.splitlines()
    if len(lines) > max_lines:
        raw = "\n".join(lines[:max_lines]).rstrip() + "\n..."
    return raw


def _status_glyph(status: str) -> str:
    return {
        "completed": "✓",
        "done": "✓",
        "failed": "✕",
        "waiting": "⏸",
        "info": "·",
        "idle": "·",
    }.get(str(status or ""), "·")


def _meta_color(status: str) -> str:
    if status == "failed":
        return Theme.RED
    if status == "completed":
        return Theme.GREEN
    if status == "running":
        return Theme.BLUE
    if status == "waiting":
        return Theme.YELLOW
    return Theme.MUTED


class _TraceTreeCompat:
    """Tiny adapter so internal smoke tests can keep querying the trace shape.

    The runtime UI is no longer a QTreeWidget, but tests still need a stable way to
    ask for top-level step count, per-step payloads, child counts, and the shared
    scrollbar that drives follow-live behavior.
    """

    def __init__(self, panel: InlineTrace) -> None:
        self._panel = panel

    def topLevelItemCount(self) -> int:  # noqa: N802 - compatibility with Qt API
        if self._panel._state.model is None:
            return 0
        return len(self._panel._cards) + 1

    def topLevelItem(self, index: int):  # noqa: N802 - compatibility with Qt API
        if index == 0 and self.topLevelItemCount() > 0:
            return _TraceCompatItem(None, self._panel)
        offset = index - 1
        if 0 <= offset < len(self._panel._cards):
            return _TraceCompatItem(self._panel._cards[offset], self._panel)
        return None

    def verticalScrollBar(self):  # noqa: N802 - compatibility with Qt API
        return self._panel._scroll.verticalScrollBar()


class _TraceCompatItem:
    def __init__(self, card: _TraceStepCard | None, panel: InlineTrace) -> None:
        self._card = card
        self._panel = panel

    def childCount(self) -> int:  # noqa: N802
        if self._card is None:
            return 0
        return len(self._card._entry.children)

    def isExpanded(self) -> bool:  # noqa: N802
        if self._card is None:
            return False
        return self._card.is_expanded()

    def data(self, _column: int, role: int):  # noqa: A003, N802 - Qt compatibility
        if role != Qt.ItemDataRole.UserRole:
            return None
        if self._card is None:
            model = self._panel._state.model
            title = localized_summary_line(model) if model is not None else ""
            return {"__summary__": True, "title": title, "status": model.overall if model is not None else "idle"}
        return self._card.trace_data()


class TraceDrawerPanel(QFrame):
    """Right-side trace drawer with bottom step-detail tray."""

    def __init__(self, host: QWidget, *, controller=None) -> None:
        super().__init__(host)
        self._host = host
        self._controller = controller
        self._fallback_session = TraceViewState() if controller is None else None
        self._owner_id = ""
        self._close_notify = None
        self._owner_ref: weakref.ReferenceType[QWidget] | None = None
        self._owner_token = 0
        self._trace_ok = True
        self._anim: QPropertyAnimation | None = None
        self.setObjectName("traceDrawerPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(Theme.SURFACE))
        self.setPalette(palette)
        self.setStyleSheet(
            f"QFrame#traceDrawerPanel {{ background: {Theme.SURFACE}; border-left: 1px solid {Theme.BORDER_SOFT}; }}"
        )
        self._build_ui()
        self.hide()

    @property
    def _session(self) -> TraceViewState:
        if self._controller is not None:
            return self._controller.session
        return self._fallback_session  # type: ignore[return-value]

    def open_from_session(self, *, animate: bool = True) -> None:
        session = self._session
        self._timeline.bind_state(session)
        self._timeline.set_events(session.events, live=session.live)
        self._refresh_chrome()
        self._detail.hide_detail(animate=False)
        self.relayout(animate=animate, raise_panel=True)

    def sync_from_session(self) -> None:
        session = self._session
        self._detail.suppress_if_unpinned()
        self.setUpdatesEnabled(False)
        try:
            self._timeline.set_events(session.events, live=session.live)
            self._refresh_chrome()
        finally:
            self.setUpdatesEnabled(True)

    def relayout(self, *, animate: bool = False, raise_panel: bool = False) -> None:
        self._relayout(animate=animate, raise_panel=raise_panel)

    def toggle_trace(
        self,
        *,
        owner_widget: QWidget,
        owner_id: str,
        events: list[dict[str, Any]],
        live: bool,
        ok: bool,
        on_close,
    ) -> bool:
        if not self.isHidden() and self._session.owner_id == str(owner_id or ""):
            self.close_panel()
            return False
        self.show_trace(
            owner_widget=owner_widget,
            owner_id=owner_id,
            events=events,
            live=live,
            ok=ok,
            on_close=on_close,
        )
        return True

    def show_trace(
        self,
        *,
        owner_widget: QWidget,
        owner_id: str,
        events: list[dict[str, Any]],
        live: bool,
        ok: bool,
        on_close,
    ) -> None:
        if self._controller is not None:
            self._controller._bind_owner(owner_widget)
            self._session.load(
                owner_id=owner_id,
                events=events,
                live=live,
                ok=ok,
                on_close=on_close,
            )
            self.open_from_session(animate=not self.isVisible())
            return
        self._notify_close()
        self._bind_owner(owner_widget)
        self._owner_id = str(owner_id or "")
        self._close_notify = on_close
        self._trace_ok = bool(ok)
        self._fallback_session.load(
            owner_id=owner_id,
            events=events,
            live=live,
            ok=ok,
            on_close=on_close,
        )
        self._timeline.bind_state(self._fallback_session)
        self._timeline.set_events(events, live=live)
        self._refresh_chrome()
        self._detail.hide_detail(animate=False)
        self.relayout(animate=not self.isVisible(), raise_panel=True)

    def update_trace(self, *, owner_id: str, events: list[dict[str, Any]], live: bool, ok: bool) -> None:
        if self.isHidden() or self._session.owner_id != str(owner_id or ""):
            return
        if self._controller is not None:
            self._session.apply_events(events, live=live, ok=ok)
            self.sync_from_session()
            return
        self._trace_ok = bool(ok)
        self._detail.suppress_if_unpinned()
        self.setUpdatesEnabled(False)
        try:
            self._fallback_session.apply_events(events, live=live, ok=ok)
            self._timeline.set_events(events, live=live)
            self._refresh_chrome()
        finally:
            self.setUpdatesEnabled(True)

    def is_owner(self, owner_id: str) -> bool:
        return (not self.isHidden()) and self._session.owner_id == str(owner_id or "")

    def depends_on(self, host: QWidget) -> bool:
        if self._controller is not None:
            return self._controller.depends_on(host)
        return _is_descendant(self._owner_widget(), host)

    def show_step_detail(self, data: dict[str, Any]) -> None:
        self._session.pin_detail(data)
        self._detail.show_detail(data)

    def close_panel(self) -> None:
        self._stop_animation()
        self.hide()
        self._detail.hide_detail(animate=False)
        if self._controller is not None:
            return
        self._unbind_owner()
        self._notify_close()

    def _refresh_chrome(self) -> None:
        session = self._session
        self._owner_id = session.owner_id
        self._trace_ok = session.ok
        self._title.setText(self._drawer_title(session.ok))
        self._summary.setText(session.summary_line())

    def _drawer_width(self) -> int:
        return min(_DRAWER_W, max(400, int(self._host.width() * 0.4)))

    def _drawer_title(self, ok: bool | None = None) -> str:
        from dbaide.i18n import t

        trace_ok = self._trace_ok if ok is None else bool(ok)
        return t("trace.view") if trace_ok else t("trace.view_failed")

    def _build_ui(self) -> None:
        from dbaide.i18n import t

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("", self)
        self._title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 13px; font-weight: 700; background: transparent;")
        top.addWidget(self._title, 1)
        close = QToolButton(self)
        close.setIcon(svg_icon("x", color=Theme.TEXT_2, size=16))
        close.setIconSize(QSize(16, 16))
        close.setToolTip(t("dialog.close"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(28, 28)
        close.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 7px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        )
        close.clicked.connect(self.close_panel)
        top.addWidget(close)
        layout.addLayout(top)

        self._summary = QLabel("", self)
        self._summary.setStyleSheet(f"color: {Theme.MUTED}; font-size: 10px; background: transparent;")
        layout.addWidget(self._summary)

        self._timeline = InlineTrace(self, show_header=False, detail_handler=self.show_step_detail)
        self._timeline.setMaximumHeight(16777215)
        layout.addWidget(self._timeline, 1)

        self._detail = _TraceDrawerDetailTray(self)
        layout.addWidget(self._detail)

    def _stop_animation(self) -> None:
        if self._anim is not None:
            try:
                self._anim.stop()
                self._anim.deleteLater()
            except RuntimeError:
                pass
            self._anim = None

    def _relayout(self, *, animate: bool, raise_panel: bool = False) -> None:
        self._stop_animation()
        host_w = max(1, int(self._host.width()))
        host_h = max(1, int(self._host.height()))
        x, y, w, h = clamp_drawer_geometry(host_w, host_h, self._drawer_width())
        target = QRect(x, y, w, h)
        was_visible = self.isVisible()
        if animate and not was_visible:
            start = QRect(host_w, y, w, h)
            self.setGeometry(start)
            self.show()
            if raise_panel:
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
            if not was_visible:
                self.show()
            if raise_panel:
                self.raise_()

    def _notify_close(self) -> None:
        callback = self._close_notify
        self._close_notify = None
        self._owner_id = ""
        if callable(callback):
            callback()

    def _owner_widget(self) -> QWidget | None:
        return _alive_widget(self._owner_ref() if self._owner_ref is not None else None)

    def _bind_owner(self, owner_widget: QWidget) -> None:
        self._unbind_owner()
        owner = _alive_widget(owner_widget)
        if owner is None:
            return
        self._owner_token += 1
        token = self._owner_token
        self._owner_ref = weakref.ref(owner)
        owner.installEventFilter(self)
        panel_ref = weakref.ref(self)

        def _on_destroyed(*_args, _token=token, _panel_ref=panel_ref) -> None:
            panel = _panel_ref()
            if panel is not None:
                panel._owner_destroyed(_token)

        owner.destroyed.connect(_on_destroyed)

    def _unbind_owner(self) -> None:
        owner = self._owner_widget()
        if owner is not None:
            try:
                owner.removeEventFilter(self)
            except RuntimeError:
                pass
        self._owner_token += 1
        self._owner_ref = None

    def _owner_destroyed(self, token: int) -> None:
        if token != self._owner_token:
            return
        self.close_panel()


class _TraceDrawerDetailTray(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._raw_text = ""
        self._copy_label = ""
        self._anim: QPropertyAnimation | None = None
        self._user_pinned = False
        self.setObjectName("traceDrawerDetail")
        self.setStyleSheet(
            f"QFrame#traceDrawerDetail {{ background: transparent; border-top: 1px solid {Theme.BORDER_SOFT}; }}"
        )
        self.setMaximumHeight(0)
        self.hide()
        self._build_ui()

    def _build_ui(self) -> None:
        from dbaide.i18n import t

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("", self)
        self._title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 12px; font-weight: 700; background: transparent;")
        head.addWidget(self._title, 1)
        close = QToolButton(self)
        close.setIcon(svg_icon("x", color=Theme.TEXT_2, size=14))
        close.setIconSize(QSize(14, 14))
        close.setToolTip(t("dialog.close"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(24, 24)
        close.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QToolButton:hover {{ background: {Theme.PANEL_2}; }}"
        )
        close.clicked.connect(self.hide_detail)
        head.addWidget(close)
        layout.addLayout(head)

        self._body = QTextBrowser()
        self._body.setFont(QFont("Inter", 10))
        configure_readonly_text_view(self._body)
        self._body.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; }")
        layout.addWidget(self._body, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self._copy_label = t("trace.copy_raw")
        self._copy_raw = ghost_action_button(
            self._copy_label,
            icon=svg_icon("copy", color=Theme.MUTED, size=14),
            tooltip=self._copy_label,
        )
        self._copy_raw.clicked.connect(self._copy)
        row.addWidget(self._copy_raw)
        layout.addLayout(row)

    def show_detail(self, data: dict[str, Any]) -> None:
        self._user_pinned = True
        self._title.setText(str(data.get("title") or data.get("phase") or data.get("stage") or "step"))
        self._raw_text = trace_step_raw_export(data)
        self._body.setHtml(_detail_html(data))
        self._copy_raw.setVisible(bool(self._raw_text.strip()))
        self._set_open(True)

    def suppress_if_unpinned(self) -> None:
        if not self._user_pinned:
            self.hide_detail(animate=False)

    def hide_detail(self, animate: bool = True) -> None:
        self._user_pinned = False
        self._set_open(False, animate=animate)

    def _copy(self) -> None:
        if not self._raw_text.strip():
            return
        QApplication.clipboard().setText(self._raw_text)

    def _set_open(self, opening: bool, *, animate: bool = True) -> None:
        if opening:
            self.show()
        end = _DRAWER_DETAIL_H if opening else 0
        if self._anim is not None:
            try:
                self._anim.stop()
                self._anim.deleteLater()
            except RuntimeError:
                pass
            self._anim = None
        self.setMaximumHeight(end)
        if not opening:
            self.hide()


class TraceDetailPanel(QFrame):
    """Right-side slide-over panel for a single trace step (one at a time per window)."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self.setObjectName("traceDetailPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(Theme.BG))
        self.setPalette(palette)
        self.setStyleSheet(
            f"QFrame#traceDetailPanel {{ background: {Theme.BG};"
            f" border-left: 1px solid {Theme.BORDER_SOFT}; }}"
        )
        self._raw_text = ""
        self._copy_label = ""
        self._copy_feedback_timer: QTimer | None = None
        self._anim: QPropertyAnimation | None = None
        self._closing = False
        self._build_ui()
        self.hide()
        host.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        event_type = event.type()
        if obj is self._host and self.isVisible() and not self._closing:
            if event_type == event.Type.Resize:
                self._relayout(animate=False)
            elif event_type in (event.Type.Hide, event.Type.Close):
                self.close_panel()
        return super().eventFilter(obj, event)

    def _build_ui(self) -> None:
        from dbaide.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("", self)
        self._title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 14px; font-weight: 700; background: transparent;"
        )
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        close = QToolButton(self)
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
        self._copy_label = t("trace.copy_raw")
        self._copy_raw = ghost_action_button(
            self._copy_label,
            icon=svg_icon("copy", color=Theme.MUTED, size=14),
            tooltip=self._copy_label,
        )
        self._copy_raw.clicked.connect(self._do_copy_raw)
        self._copy_raw.hide()
        row.addWidget(self._copy_raw)
        layout.addLayout(row)

    def show_detail(self, data: dict) -> None:
        self._closing = False
        self._reset_copy_feedback()
        title = str(data.get("title") or data.get("phase") or data.get("stage") or "step")
        self._title.setText(title)
        self._raw_text = trace_step_raw_export(data)
        self._body.setHtml(_detail_html(data))
        self._copy_raw.setVisible(bool(self._raw_text.strip()))
        self._relayout(animate=True)

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self._closing = True
        self._stop_animation()
        # This legacy detail slide-over is not the primary trace surface anymore.
        # Closing it synchronously avoids Qt lifetime races between the animation
        # callback and host/widget teardown during markdown/web render tests.
        self.hide()
        self._closing = False

    def _panel_width(self) -> int:
        return min(440, max(320, int(self._host.width() * 0.36)))

    def _stop_animation(self) -> None:
        if self._anim is not None:
            try:
                self._anim.stop()
                self._anim.deleteLater()
            except RuntimeError:
                pass
            self._anim = None

    def _relayout(self, *, animate: bool) -> None:
        if self._closing:
            return
        self._stop_animation()
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
        if not self._raw_text.strip():
            return
        QApplication.clipboard().setText(self._raw_text)
        from dbaide.i18n import t
        self._copy_raw.setText(t("ask.copied"))
        self._copy_raw.setIcon(svg_icon("check", color=Theme.GREEN, size=14))
        self._reset_copy_feedback_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._reset_copy_feedback)
        timer.start(1200)
        self._copy_feedback_timer = timer

    def _reset_copy_feedback_timer(self) -> None:
        if self._copy_feedback_timer is not None:
            try:
                self._copy_feedback_timer.stop()
                self._copy_feedback_timer.deleteLater()
            except RuntimeError:
                pass
            self._copy_feedback_timer = None

    def _reset_copy_feedback(self) -> None:
        self._reset_copy_feedback_timer()
        try:
            from PyQt6 import sip
            if sip.isdeleted(self._copy_raw):
                return
        except RuntimeError:
            return
        if not self._copy_label:
            from dbaide.i18n import t
            self._copy_label = t("trace.copy_raw")
        self._copy_raw.setText(self._copy_label)
        self._copy_raw.setIcon(svg_icon("copy", color=Theme.MUTED, size=14))


def show_trace_detail(host: QWidget, data: dict) -> None:
    """Show step detail inside the active trace drawer (never a stray slide-over)."""
    from dbaide.desktop.trace.overlay import show_trace_detail as _show

    _show(host, data)


def toggle_trace_drawer(
    host: QWidget,
    *,
    owner_widget: QWidget,
    owner_id: str,
    events: list[dict[str, Any]],
    live: bool,
    ok: bool,
    on_close,
) -> bool:
    from dbaide.desktop.trace.overlay import toggle_trace_drawer as _toggle

    return _toggle(
        host,
        owner_widget=owner_widget,
        owner_id=owner_id,
        events=events,
        live=live,
        ok=ok,
        on_close=on_close,
    )


def update_trace_drawer(
    host: QWidget,
    *,
    owner_id: str,
    events: list[dict[str, Any]],
    live: bool,
    ok: bool,
) -> None:
    from dbaide.desktop.trace.overlay import update_trace_drawer as _update

    _update(
        host,
        owner_id=owner_id,
        events=events,
        live=live,
        ok=ok,
    )


def trace_step_raw_export(data: dict[str, Any]) -> str:
    """Clipboard JSON for a trace step — includes display fields plus the raw event."""
    if not isinstance(data, dict):
        return ""
    if data.get("__summary__"):
        try:
            return json.dumps(data, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            return str(data)
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    payload: dict[str, Any] = {}
    for key in (
        "node_id", "stage", "phase", "agent", "status", "title", "detail",
        "thought", "duration_ms", "step", "node_type",
    ):
        value = data.get(key)
        if value not in (None, "", {}, []):
            payload[key] = value
    if raw:
        payload["event"] = raw
    if not payload:
        return ""
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(payload)


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


_MAX_DETAIL_JSON = 20_000  # chars; the full event is always available via "Copy raw"


def _json_text(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            text = str(value)
    # Bound the rendered size so a step carrying a large output/result_data payload
    # can't freeze the detail panel's QTextBrowser. Copy raw still exports it in full.
    if len(text) > _MAX_DETAIL_JSON:
        from dbaide.i18n import t
        return text[:_MAX_DETAIL_JSON] + "\n… " + t("trace.detail.truncated", n=f"{len(text):,}")
    return text


def _section(title: str, body: str, *, code: bool = False) -> str:
    if not str(body or "").strip():
        return ""
    head = f"<div style='color:{Theme.MUTED}; font-size:11px; font-weight:600; margin-top:10px; margin-bottom:4px;'>{_esc(title)}</div>"
    if code:
        return head + _code_block(body)
    return head + f"<div style='color:{Theme.TEXT}; font-size:12px; line-height:1.45; white-space:pre-wrap;'>{_esc(body)}</div>"


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
        f"<pre style='background:transparent; border:none; margin:0; padding:0;"
        f" font-family:Menlo,monospace; font-size:11px;"
        f" color:{Theme.TEXT}; white-space:pre-wrap;'>{body}</pre>"
    )


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
