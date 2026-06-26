"""Single-view conversation: the whole transcript in ONE QWebEngineView.

A drop-in replacement for ``ConversationView`` (same public API) that renders the
entire chat as one HTML document (``dbaide.rendering.conversation_page``) instead of
one WebEngine view per answer. QtWebEngine never reclaims a view's renderer process,
so the per-answer model grew memory ~per answer and unbounded; this keeps it to a
single view. See project memory ``webengine-process-memory``.

The Python side owns a ``ConversationModel`` (a list of turn dicts) and pushes
updates into ``window.DBChat`` via runJavaScript; user interactions (clarification
submit, action buttons, trace toggle) return over a QWebChannel bridge.

Without WebEngine available (unit tests) the model still works fully — only the JS
push is a no-op — so the facade is testable headless.
"""

from __future__ import annotations

import json
import time
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from dbaide.agent.agenda import latest_agenda_from_events
from dbaide.agent.progress_events import conversation_trace_step, phase_for
from dbaide.agent.trace_model import count_timeline_steps
from dbaide.desktop.components.markdown_webview import try_create_webengine_view
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import (
    echarts_script_src,
    hljs_script_src,
    marked_script_src,
    webengine_html_base,
)
from dbaide.rendering.compose import compose_blocks
from dbaide.rendering.conversation_page import build_conversation_page


def _theme_payload() -> dict[str, str]:
    """The live app palette → the conversation page's CSS variables (so the chat
    matches the current theme and re-styles when it changes)."""
    return {
        "text": Theme.TEXT, "text2": Theme.TEXT_2, "muted": Theme.MUTED,
        "bg": Theme.BG, "panel": Theme.PANEL, "panel2": Theme.PANEL_2,
        "panel3": getattr(Theme, "PANEL_3", Theme.PANEL_2), "border": Theme.BORDER_SOFT,
        "codeBg": Theme.CODE_BG, "accent": Theme.ACCENT,
        "accentText": getattr(Theme, "ACCENT_TEXT", "#ffffff"), "link": Theme.BLUE,
        "blue": Theme.BLUE, "green": Theme.GREEN, "yellow": Theme.YELLOW, "red": Theme.RED,
    }


def _thinking_label() -> str:
    try:
        from dbaide.i18n import t
        return t("status.thinking") or "Thinking…"
    except Exception:  # noqa: BLE001
        return "Thinking…"


def _resolve_final_answer(answer: str, live_text: str) -> str:
    """Pick the best text when the streamed and final payloads disagree."""
    authoritative = str(answer or "")
    streamed = str(live_text or "")
    if not authoritative:
        return streamed
    if not streamed or len(authoritative) >= len(streamed):
        return authoritative
    if authoritative.startswith(streamed) or streamed.startswith(authoritative):
        return streamed if len(streamed) > len(authoritative) else authoritative
    return authoritative


def _agenda_dicts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for item in latest_agenda_from_events(events) or []:
            out.append({
                "id": getattr(item, "id", "") or getattr(item, "title", ""),
                "title": getattr(item, "title", ""),
                "status": getattr(item, "status", "pending"),
                "kind": getattr(item, "kind", ""),
                "acceptance": getattr(item, "acceptance", ""),
            })
    except Exception:  # noqa: BLE001
        return []
    return out


class _ClarificationProxy(QObject):
    """Returned by ``append_clarification`` so callers can ``.submitted.connect(...)``
    exactly as with the old ``_ClarificationBar``; fired when the page submits."""

    submitted = pyqtSignal(str)


class _ConversationBridge(QObject):
    """The page's single callback object (QWebChannel). Carries user interactions back
    to Python; never accepts raw SQL or HTML."""

    clarifySubmitted = pyqtSignal(str, str)   # (turn_id, text)
    actionInvoked = pyqtSignal(str, str)      # (turn_id, action_id)
    traceToggled = pyqtSignal(str)            # (turn_id,)

    @pyqtSlot(str, str)
    def clarify(self, turn_id: str, text: str) -> None:
        self.clarifySubmitted.emit(str(turn_id), str(text))

    @pyqtSlot(str, str)
    def action(self, turn_id: str, action_id: str) -> None:
        self.actionInvoked.emit(str(turn_id), str(action_id))

    @pyqtSlot(str)
    def toggleTrace(self, turn_id: str) -> None:  # noqa: N802 (matches JS call)
        self.traceToggled.emit(str(turn_id))


def _js_arg(obj: Any) -> str:
    """JSON encode for embedding as a runJavaScript argument. U+2028/U+2029 are valid
    in JSON but were invalid in JS string literals before ES2019 — escape them."""
    return (
        json.dumps(obj, ensure_ascii=False, default=str)
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


_INTERNAL_KEYS = ("_events", "_start", "_answer", "_final")


def _public(turn: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in turn.items() if k not in _INTERNAL_KEYS}


class ConversationWebView(QWidget):
    """One WebEngine view rendering the whole conversation. API-compatible with
    ``ConversationView`` (the multi-widget implementation it replaces)."""

    _STREAM_FLUSH_MS = 80

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._turns: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._current: dict[str, Any] | None = None
        self._turn_seq = 0
        self._hint = ""
        self._bulk = 0
        self._ready = False
        self._pending_js: list[str] = []
        # streaming coalescing
        self._stream_full = ""
        self._stream_pushed = 0
        from PyQt6.QtCore import QTimer
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(self._STREAM_FLUSH_MS)
        self._stream_timer.timeout.connect(self._flush_stream)
        # clarification proxies kept alive per turn so the signal survives until answered
        self._clarify_proxies: dict[str, _ClarificationProxy] = {}
        # per-turn action dispatcher: action_id -> runs the (copy / chart dialog / build /
        # export) handler the caller registered for that turn
        self._action_handlers: dict[str, Any] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._view: Any = None
        self._bridge: _ConversationBridge | None = None
        self._channel: Any = None
        view_cls = try_create_webengine_view()
        if view_cls is not None:
            self._view = view_cls(self)
            lay.addWidget(self._view)
            self._wire_bridge()
            self._load_page()
        # else: headless / no WebEngine — model works, JS pushes are no-ops.

    # ── page + bridge setup ────────────────────────────────────────────────
    def _wire_bridge(self) -> None:
        try:
            from PyQt6.QtWebChannel import QWebChannel
        except Exception:  # noqa: BLE001
            return
        self._bridge = _ConversationBridge(self)
        self._bridge.clarifySubmitted.connect(self._on_clarify)
        self._bridge.actionInvoked.connect(self._on_action)
        self._bridge.traceToggled.connect(self._on_trace_toggle)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

    def _load_page(self) -> None:
        es, ms, hs = echarts_script_src(), marked_script_src(), hljs_script_src()
        html = build_conversation_page(_theme_payload(), marked_src=ms, hljs_src=hs, echarts_src=es)
        load_finished = getattr(self._view, "loadFinished", None)
        if load_finished is not None:
            load_finished.connect(self._on_loaded)
        self._view.setHtml(html, webengine_html_base(es, ms, hs))

    def _on_loaded(self, ok: bool = True) -> None:
        self._ready = True
        for code in self._pending_js:
            self._exec_js(code)
        self._pending_js.clear()

    def _exec_js(self, code: str) -> None:
        try:
            self._view.page().runJavaScript(code)
        except Exception:  # noqa: BLE001
            pass

    def _run_js(self, code: str) -> None:
        if self._view is None:
            return
        if not self._ready:
            self._pending_js.append(code)
            return
        self._exec_js(code)

    def _push(self, method: str, *args: Any) -> None:
        payload = ", ".join(_js_arg(a) for a in args)
        self._run_js(f"window.DBChat && window.DBChat.{method}({payload});")

    def _sync(self, turn: dict[str, Any]) -> None:
        if self._bulk:
            return
        self._push("setTurn", _public(turn))

    # ── turn lifecycle (ConversationView API) ──────────────────────────────
    def begin_turn(self, user_text: str, *, meta: str = "", placeholder: bool = True,
                   attachments: list[dict] | None = None) -> None:
        self._stream_timer.stop()
        self._flush_stream()
        self._stream_full = ""
        self._stream_pushed = 0
        self._turn_seq += 1
        tid = f"t{self._turn_seq}"
        user = None
        if str(user_text or "") or attachments:
            user = {"text": str(user_text or ""), "meta": str(meta or ""),
                    "attachments": [{"name": str((a or {}).get("name") or (a or {}).get("table") or "")}
                                    for a in (attachments or [])]}
        turn = {
            "id": tid, "user": user,
            "status": {"state": "running", "phase": _thinking_label()} if placeholder else None,
            "agenda": [], "blocks": None, "stream": None, "notes": [],
            "clarification": None, "actions": [],
            "_events": [], "_start": time.monotonic(), "_answer": "", "_final": "",
        }
        self._turns.append(turn)
        self._by_id[tid] = turn
        self._current = turn
        if self._hint:
            self._hint = ""
            self._push("setHint", "")
        self._sync(turn)

    def append_answer_chunk(self, text: str) -> None:
        if not text or self._current is None:
            return
        self._stream_full += str(text)
        self._current["_answer"] = self._stream_full
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream(self) -> None:
        if self._current is None:
            return
        delta = self._stream_full[self._stream_pushed:]
        if not delta:
            return
        self._stream_pushed = len(self._stream_full)
        self._push("appendStream", self._current["id"], delta)

    def append_trace(self, message: str, *, kind: str = "", detail: str = "") -> None:
        if self._current is None:
            self.begin_turn("")
        turn = self._current
        if turn is None or not message:
            return
        turn["status"] = {"state": "running", "phase": str(message)}
        self._sync(turn)

    def append_trace_event(self, event: dict[str, Any]) -> None:
        if self._current is None:
            self.begin_turn("")
        turn = self._current
        if turn is None:
            return
        turn["_events"].append(dict(event or {}))
        phase = phase_for(str((event or {}).get("stage") or ""))
        if not phase:
            step = conversation_trace_step(event)
            phase = step[0] if step else ""
        if phase:
            turn["status"] = {"state": "running", "phase": str(phase)}
        turn["agenda"] = _agenda_dicts(turn["_events"])
        self._sync(turn)

    def complete_turn(self, *, answer: str = "", trace_events: list[dict[str, Any]] | None = None,
                      warnings: list[str] | None = None, errors: list[str] | None = None,
                      workflow_id: str = "", ok: bool = True, actions_widget: QWidget | None = None,
                      charts: list[dict[str, Any]] | None = None,
                      actions: list[dict[str, Any]] | None = None, on_action: Any = None) -> None:
        if self._current is None:
            self.begin_turn("")
        turn = self._current
        if turn is None:
            return
        self._stream_timer.stop()
        self._flush_stream()
        events = list(trace_events) if trace_events else list(turn["_events"])
        final = _resolve_final_answer(answer, turn["_answer"])
        chart_list = [c for c in (charts or []) if isinstance(c, dict) and c.get("chart_id")]
        blocks = compose_blocks(final, chart_list, theme=_theme_payload()) if (final.strip() or chart_list) else []
        notes: list[dict[str, str]] = []
        for w in (warnings or []):
            if str(w).strip():
                notes.append({"kind": "warning", "text": str(w)})
        for e in (errors or []):
            if str(e).strip():
                notes.append({"kind": "error", "text": str(e)})
        steps = 0
        try:
            steps = int(count_timeline_steps(events))
        except Exception:  # noqa: BLE001
            steps = 0
        turn.update({
            "blocks": blocks, "stream": None, "notes": notes,
            "agenda": _agenda_dicts(events), "clarification": None,
            "actions": list(actions or []),
            "status": {"state": "done" if ok else "error", "steps": steps,
                       "seconds": round(time.monotonic() - turn["_start"], 1), "ok": bool(ok)},
            "_events": events, "_final": final,
        })
        if on_action is not None:
            self._action_handlers[turn["id"]] = on_action
        self._current = None
        self._sync(turn)

    def _on_action(self, turn_id: str, action_id: str) -> None:
        handler = self._action_handlers.get(turn_id)
        if handler is not None:
            try:
                handler(str(action_id))
            except Exception:  # noqa: BLE001
                pass

    def _on_trace_toggle(self, turn_id: str) -> None:
        turn = self._by_id.get(turn_id)
        if turn is None:
            return
        try:
            from dbaide.desktop.components.trace import toggle_trace_drawer
            toggle_trace_drawer(
                self, owner_widget=self, owner_id=str(turn_id),
                events=list(turn.get("_events") or []), live=False,
                ok=bool((turn.get("status") or {}).get("ok", True)),
                on_close=lambda: None,
            )
        except Exception:  # noqa: BLE001
            pass

    def finish_turn_error(self, message: str) -> None:
        self._stream_timer.stop()
        turn = self._current
        if turn is None:
            return
        turn["status"] = {"state": "error", "phase": str(message or "Failed"), "ok": False}
        turn["notes"] = [{"kind": "error", "text": str(message or "")}]
        turn["clarification"] = None
        turn["_final"] = turn.get("_answer", "")
        self._current = None
        self._sync(turn)

    # ── clarification ──────────────────────────────────────────────────────
    def append_clarification(self, *, question: str, options: list[str],
                             questions: list[dict] | None = None) -> _ClarificationProxy:
        if self._current is None:
            self.begin_turn("")
        turn = self._current
        proxy = _ClarificationProxy(self)
        if turn is None:
            return proxy
        multi = bool(questions) and len(questions) > 1
        if multi:
            clar = {"mode": "multi", "questions": [
                {"question": str(q.get("question") or ""), "options": list(q.get("options") or [])}
                for q in (questions or [])], "done": False}
        else:
            clar = {"mode": "single", "question": str(question or ""),
                    "options": list(options or []), "done": False}
        turn["clarification"] = clar
        turn["status"] = {"state": "waiting", "phase": "Waiting for reply"}
        self._clarify_proxies[turn["id"]] = proxy
        self._sync(turn)
        return proxy

    def append_clarification_reply(self, text: str) -> None:
        turn = self._current
        if turn is None:
            return
        turn["clarification"] = None
        # surface the chosen reply as a trailing user line on the same turn
        user = turn.get("user") or {"text": "", "meta": "", "attachments": []}
        extra = str(text or "")
        if extra:
            user = dict(user)
            user["text"] = (str(user.get("text") or "") + ("\n" if user.get("text") else "") + extra).strip()
            turn["user"] = user
        self._sync(turn)

    def _on_clarify(self, turn_id: str, text: str) -> None:
        proxy = self._clarify_proxies.pop(turn_id, None)
        turn = self._by_id.get(turn_id)
        if turn is not None and turn.get("clarification"):
            turn["clarification"] = None
        if proxy is not None:
            proxy.submitted.emit(str(text))

    # ── session / misc ─────────────────────────────────────────────────────
    def has_open_turn(self) -> bool:
        st = (self._current or {}).get("status") if self._current else None
        return bool(st and st.get("state") in ("running", "waiting"))

    def append_hint(self, text: str) -> None:
        self._hint = str(text or "")
        self._push("setHint", self._hint)

    def begin_bulk_load(self) -> None:
        self._bulk += 1

    def end_bulk_load(self) -> None:
        self._bulk = max(0, self._bulk - 1)
        if self._bulk == 0:
            self._push("render", [_public(t) for t in self._turns])

    def clear(self) -> None:
        self._stream_timer.stop()
        self._turns = []
        self._by_id = {}
        self._current = None
        self._clarify_proxies = {}
        self._action_handlers = {}
        self._stream_full = ""
        self._stream_pushed = 0
        self._hint = ""
        self._push("clearAll")

    def copy_text(self) -> str:
        parts: list[str] = []
        for turn in self._turns:
            user = (turn.get("user") or {}).get("text") or ""
            if user:
                parts.append(user)
            final = turn.get("_final") or turn.get("_answer") or ""
            if final:
                parts.append(final)
            parts.append("---")
        return "\n\n".join(parts).strip()

    def refresh_theme(self) -> None:
        """Push the current app palette to the page (call after a theme switch)."""
        self._push("setTheme", _theme_payload())

    def shutdown(self) -> None:
        self._stream_timer.stop()
