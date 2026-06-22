from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PyQt6 import sip

from dbaide.desktop.task_manager import TaskHandle


@dataclass
class ModeUiState:
    index: int
    mode: str


@dataclass
class ComposerUiState:
    has_connection: bool
    busy: bool
    waiting_for_reply: bool
    placeholder: str


@dataclass
class RunStatusUiState:
    topbar_text: str
    topbar_state: str
    running_ids: set[str] = field(default_factory=set)
    pending_rows: list[dict[str, Any]] = field(default_factory=list)
    selected_chat: str = ""


@dataclass
class ConversationSlotState:
    question: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    connection: str = ""
    pending_resume: dict[str, Any] | None = None

    def is_empty(self) -> bool:
        return not (
            self.question
            or self.trace
            or self.session_id
            or self.connection
            or self.pending_resume
        )


@dataclass
class OneOffState:
    action: str = ""
    sql_doc: Any | None = None
    data_doc: Any | None = None
    sql: str = ""
    connection: str = ""
    database: str = ""


@dataclass
class OneOffRunState:
    current: OneOffState = field(default_factory=OneOffState)
    handle: TaskHandle | None = None
    building: bool = False

    def begin(self, state: OneOffState) -> None:
        self.current = state
        self.handle = None
        self.building = state.action == "build_assets"

    def attach_handle(self, handle: TaskHandle) -> None:
        self.handle = handle

    def finish(self) -> None:
        self.current = OneOffState()
        self.handle = None
        self.building = False


@dataclass
class ConversationRunState:
    max_runs: int = 4
    runs: dict[str, TaskHandle] = field(default_factory=dict)
    queue: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    slots: dict[str, ConversationSlotState] = field(default_factory=dict)
    new_counter: int = 0
    active_key: str = ""

    def reset(self) -> None:
        self.runs.clear()
        self.queue.clear()
        self.slots.clear()
        self.active_key = ""

    def new_slot_key(self) -> str:
        self.new_counter += 1
        return f"new:{self.new_counter}"

    def active_or_new_key(self) -> str:
        if not self.active_key:
            self.active_key = self.new_slot_key()
        return self.active_key

    def queue_run(self, key: str, payload: dict[str, Any]) -> None:
        self.remove_queued(key)
        self.queue.append((key, dict(payload or {})))

    def remove_queued(self, key: str) -> None:
        self.queue = [(k, payload) for k, payload in self.queue if k != key]

    def active_count(self) -> int:
        return len(self.runs) + len(self.queue)

    def running_ids(self) -> set[str]:
        ids = set(self.runs.keys())
        ids.update(key for key, _payload in self.queue)
        return ids

    def is_active_running(self) -> bool:
        return bool(self.active_key and self.active_key in self.runs)

    def is_active_queued(self) -> bool:
        return bool(self.active_key and any(key == self.active_key for key, _payload in self.queue))

    def is_active_waiting(self) -> bool:
        return bool(self.active_key and self.pending_resume_for(self.active_key))

    def pending_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key in [*self.runs.keys(), *(queued_key for queued_key, _payload in self.queue)]:
            if key in seen or not key.startswith("new:"):
                continue
            seen.add(key)
            rows.append({"key": key, "title": self.question_for(key)})
        return rows

    def remap(self, old: str, new: str) -> None:
        if not old or not new or old == new:
            return
        if self.active_key == old:
            self.active_key = new
        self._move_key(self.runs, old, new)
        self._move_key(self.slots, old, new)
        self.queue = [(new if key == old else key, payload) for key, payload in self.queue]

    @staticmethod
    def _move_key(mapping: dict[str, Any], old: str, new: str) -> None:
        # On collision the *old* entry is the live one being renamed (e.g. a new chat's
        # temp slot whose server session_id turns out to already have a stale slot), so
        # it wins — the previous code popped `old` but kept `new`, silently discarding
        # the live slot's accumulated state (orphaned conversation).
        if old not in mapping:
            return
        mapping[new] = mapping.pop(old)

    def slot(self, key: str) -> ConversationSlotState | None:
        return self.slots.get(str(key))

    def ensure_slot(self, key: str) -> ConversationSlotState:
        slot_key = str(key or "")
        if not slot_key:
            raise ValueError("slot key is required")
        return self.slots.setdefault(slot_key, ConversationSlotState())

    def activate(self, key: str) -> None:
        self.active_key = str(key or "")

    def clear_active(self) -> None:
        self.active_key = ""

    def question_for(self, key: str) -> str:
        slot = self.slot(key)
        return str(slot.question) if slot is not None else ""

    def set_question(self, key: str, question: str) -> None:
        self.ensure_slot(key).question = str(question or "")

    def trace_for(self, key: str) -> list[dict[str, Any]]:
        slot = self.slot(key)
        return list(slot.trace) if slot is not None else []

    def set_trace(self, key: str, events: list[dict[str, Any]]) -> None:
        self.ensure_slot(key).trace = [dict(item) for item in (events or []) if isinstance(item, dict)]

    def append_trace_event(self, key: str, event: dict[str, Any]) -> None:
        self.ensure_slot(key).trace.append(dict(event))

    def session_for(self, key: str) -> str:
        slot = self.slot(key)
        return str(slot.session_id) if slot is not None else ""

    def set_session(self, key: str, session_id: str) -> None:
        self.ensure_slot(key).session_id = str(session_id or "")

    def connection_for(self, key: str) -> str:
        slot = self.slot(key)
        return str(slot.connection) if slot is not None else ""

    def set_connection(self, key: str, connection: str) -> None:
        self.ensure_slot(key).connection = str(connection or "")

    def pending_resume_for(self, key: str) -> dict[str, Any] | None:
        slot = self.slot(key)
        if slot is None or not isinstance(slot.pending_resume, dict):
            return None
        return dict(slot.pending_resume)

    def set_pending_resume(self, key: str, resume_state: dict[str, Any] | None) -> None:
        slot = self.ensure_slot(key)
        slot.pending_resume = dict(resume_state or {}) if resume_state else None

    def clear_pending_resume(self, key: str) -> None:
        slot = self.slot(key)
        if slot is None:
            return
        slot.pending_resume = None
        self._prune_slot(str(key))

    def clear_runtime(self, key: str) -> None:
        slot = self.slot(key)
        if slot is None:
            return
        slot.question = ""
        slot.trace = []
        slot.connection = ""
        slot.pending_resume = None
        self._prune_slot(str(key))

    def discard_slot(self, key: str) -> None:
        slot_key = str(key or "")
        self.runs.pop(slot_key, None)
        self.remove_queued(slot_key)
        self.slots.pop(slot_key, None)
        if self.active_key == slot_key:
            self.active_key = ""

    def active_debug_context(self, *, connection_name: str, session_id: str) -> dict[str, Any]:
        key = self.active_key
        return {
            "connection_name": str(connection_name or ""),
            "session_id": str(session_id or ""),
            "active_slot": key,
            "trace": self.trace_for(key) if key else [],
            "question": self.question_for(key) if key else "",
        }

    def _prune_slot(self, key: str) -> None:
        slot = self.slots.get(key)
        if slot is None:
            return
        if key == self.active_key:
            return
        if key in self.runs or any(queued_key == key for queued_key, _ in self.queue):
            return
        if slot.is_empty():
            self.slots.pop(key, None)


@dataclass(frozen=True)
class BackgroundWorkItem:
    action: str
    connection: str
    label: str


@dataclass
class BackgroundWorkState:
    items: list[BackgroundWorkItem] = field(default_factory=list)

    def push(self, action: str, connection: str, label: str) -> None:
        self.items.append(BackgroundWorkItem(str(action or ""), str(connection or ""), str(label or "")))

    def pop(self, action: str, connection: str = "") -> None:
        target_action = str(action or "")
        target_conn = str(connection or "")
        for index in range(len(self.items) - 1, -1, -1):
            item = self.items[index]
            if item.action != target_action:
                continue
            if target_conn and item.connection and item.connection != target_conn:
                continue
            self.items.pop(index)
            return

    def clear(self) -> None:
        self.items.clear()

    def label_for(self, connection: str) -> str:
        target = str(connection or "")
        if not target:
            return ""
        for item in reversed(self.items):
            if item.connection == target:
                return item.label
        return ""

    def busy(self, connection: str = "") -> bool:
        target = str(connection or "")
        if not target:
            return bool(self.items)
        return any(item.connection == target for item in self.items)


class UiStateBinder:
    """Applies already-computed UI state to Qt widgets.

    This layer deliberately has no business decisions. It keeps widget mutation in
    one place so worker lifecycle state, busy indicators, and sidebar selection stay
    synchronized.
    """

    def __init__(self, window: Any) -> None:
        self.window = window

    def apply_mode(self, state: ModeUiState) -> None:
        win = self.window
        if self._usable(getattr(win, "stack", None)) and win.stack.currentIndex() != state.index:
            win.stack.setCurrentIndex(state.index)
        if self._usable(getattr(win, "composer", None)):
            win.composer.setVisible(state.mode == "Assistant")
        if self._usable(getattr(win, "sidebar", None)):
            win.sidebar.set_mode(state.mode)

    def apply_composer(self, state: ComposerUiState) -> None:
        composer = getattr(self.window, "composer", None)
        if not self._usable(composer):
            return
        # Parent enable first, then per-child running — otherwise Qt re-enables
        # children when the parent is turned back on and the input stays editable
        # while the busy placeholder/spinner is still showing.
        composer.set_placeholder(state.placeholder)
        composer.setEnabled(state.has_connection)
        composer.set_running(state.busy)

    def apply_run_status(self, state: RunStatusUiState) -> None:
        self._set_topbar_status(state.topbar_text, state.topbar_state)
        self.apply_chat_activity(state.running_ids, state.pending_rows, state.selected_chat)

    def apply_chat_activity(
        self,
        running_ids: set[str],
        pending_rows: list[dict[str, Any]],
        selected_chat: str,
    ) -> None:
        chats = getattr(getattr(self.window, "sidebar", None), "chats", None)
        if not self._usable(chats):
            return
        chats.set_pending(list(pending_rows or []))
        chats.set_running(set(running_ids or set()))
        chats.set_current(selected_chat)

    def restore_connection_status(self, connection: str, connections: list[dict[str, Any]]) -> None:
        status = "missing"
        for item in connections or []:
            if str(item.get("name") or "") == connection:
                status = str(item.get("asset_status") or "missing")
                break
        topbar = getattr(self.window, "topbar", None)
        if self._usable(topbar):
            topbar.set_asset_status(status)

    def schema_loading(self, message: str, *, update: bool = False) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if not self._usable(sidebar):
            return
        if update and hasattr(sidebar, "update_loading"):
            sidebar.update_loading(str(message or ""))
        else:
            sidebar.set_loading(str(message or ""))

    def schema_loaded(self, rows: list[dict[str, Any]], completion: dict[str, Any]) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar):
            sidebar.load_schema(list(rows or []))
        workbench = getattr(self.window, "workbench", None)
        if self._usable(workbench):
            workbench.set_sql_schema(dict(completion or {}))

    def schema_error(self, message: str) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar):
            sidebar.load_schema([], error=str(message or ""))

    def schema_build_progress_start(self, message: str = "") -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar) and hasattr(sidebar, "start_build_progress"):
            sidebar.start_build_progress(str(message or ""))

    def schema_build_progress_update(self, message: object) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar) and hasattr(sidebar, "update_build_progress"):
            sidebar.update_build_progress(message)

    def schema_build_progress_finish(self, message: str = "", *, failed: bool = False) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar) and hasattr(sidebar, "finish_build_progress"):
            sidebar.finish_build_progress(str(message or ""), failed=failed)

    def set_doc_running(self, doc: Any, running: bool) -> None:
        if self._usable(doc) and hasattr(doc, "set_running"):
            doc.set_running(bool(running))

    def set_settings_busy(self, dialog: Any, action: str, busy: bool, *, target: str = "connection") -> None:
        if not self._usable(dialog):
            return
        method_name = "set_test_busy" if str(action or "") == "test" else "set_save_busy"
        method = getattr(dialog, method_name, None)
        if callable(method):
            method(bool(busy), target=str(target or "connection"))

    def set_node_refreshing(self, node: dict[str, Any] | str, refreshing: bool) -> None:
        sidebar = getattr(self.window, "sidebar", None)
        if self._usable(sidebar) and hasattr(sidebar, "set_node_refreshing"):
            sidebar.set_node_refreshing(node, bool(refreshing))

    def global_status(self, text: str, state: str = "idle") -> None:
        self._set_topbar_status(text, state)

    def statusbar_message(self, message: str, timeout_ms: int = 0) -> None:
        statusbar = getattr(self.window, "statusbar", None)
        if self._usable(statusbar):
            statusbar.showMessage(str(message or ""), int(timeout_ms))

    def toast(self, message: str) -> None:
        self.statusbar_message(message, 5000)

    def _set_topbar_status(self, text: str, state: str) -> None:
        topbar = getattr(self.window, "topbar", None)
        if self._usable(topbar):
            topbar.set_global_status(str(text or ""), str(state or "idle"))

    @staticmethod
    def _usable(widget: Any) -> bool:
        if widget is None:
            return False
        try:
            return not sip.isdeleted(widget)
        except TypeError:
            return True
