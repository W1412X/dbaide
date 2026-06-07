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
    pending_resume: dict[str, dict[str, Any]] = field(default_factory=dict)
    slot_trace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    slot_question: dict[str, str] = field(default_factory=dict)
    slot_session: dict[str, str] = field(default_factory=dict)
    slot_connection: dict[str, str] = field(default_factory=dict)
    new_counter: int = 0
    active_key: str = ""

    def reset(self) -> None:
        self.runs.clear()
        self.queue.clear()
        self.pending_resume.clear()
        self.slot_trace.clear()
        self.slot_question.clear()
        self.slot_session.clear()
        self.slot_connection.clear()
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
        return bool(self.active_key and self.active_key in self.pending_resume)

    def pending_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key in [*self.runs.keys(), *(queued_key for queued_key, _payload in self.queue)]:
            if key in seen or not key.startswith("new:"):
                continue
            seen.add(key)
            rows.append({"key": key, "title": self.slot_question.get(key, "")})
        return rows

    def remap(self, old: str, new: str) -> None:
        if not old or not new or old == new:
            return
        if self.active_key == old:
            self.active_key = new
        self._move_key(self.runs, old, new)
        self._move_key(self.pending_resume, old, new)
        self._move_key(self.slot_trace, old, new)
        self._move_key(self.slot_question, old, new)
        self._move_key(self.slot_session, old, new)
        self._move_key(self.slot_connection, old, new)
        self.queue = [(new if key == old else key, payload) for key, payload in self.queue]

    @staticmethod
    def _move_key(mapping: dict[str, Any], old: str, new: str) -> None:
        if old not in mapping:
            return
        value = mapping.pop(old)
        if new not in mapping:
            mapping[new] = value


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
        composer.set_running(state.busy)
        composer.set_placeholder(state.placeholder)
        composer.setEnabled(state.has_connection)

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
