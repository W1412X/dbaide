from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from PyQt6.QtCore import QObject, QThreadPool

from dbaide.desktop.service import DesktopService
from dbaide.desktop.workers import ServiceWorker


DoneCallback = Callable[[Any], None]
FailedCallback = Callable[[object], None]
ProgressCallback = Callable[[object], None]


@dataclass
class TaskHandle:
    """Stable UI-side reference for one background service call."""

    id: str
    action: str
    worker: ServiceWorker
    metadata: dict[str, Any] = field(default_factory=dict)

    def cancel(self) -> None:
        self.worker.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self.worker.is_cancelled


class TaskManager(QObject):
    """Owns desktop worker lifetime and callback routing.

    UI code should not retain raw ServiceWorker objects. A TaskHandle gives callers
    cancellation and identity while this manager keeps the runnable/signals alive
    until a terminal signal arrives.
    """

    def __init__(self, service: DesktopService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.pool = QThreadPool.globalInstance()
        self._tasks: dict[str, TaskHandle] = {}

    def start(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        on_done: DoneCallback,
        on_failed: FailedCallback,
        on_progress: ProgressCallback | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskHandle:
        worker = ServiceWorker(self.service, action, payload)
        handle = TaskHandle(
            id=f"{action}:{uuid4().hex}",
            action=action,
            worker=worker,
            metadata=dict(metadata or {}),
        )
        self._tasks[handle.id] = handle

        worker.signals.done.connect(
            lambda emitted_action, result, task_id=handle.id: self._finish_done(
                task_id, emitted_action, result, on_done
            )
        )
        worker.signals.failed.connect(
            lambda exc, task_id=handle.id: self._finish_failed(task_id, exc, on_failed)
        )
        if on_progress is not None:
            worker.signals.progress.connect(
                lambda message, task_id=handle.id: self._route_progress(task_id, message, on_progress)
            )
        self.pool.start(worker)
        return handle

    def contains(self, handle: TaskHandle | None) -> bool:
        return bool(handle and handle.id in self._tasks)

    def cancel(self, handle: TaskHandle | None) -> bool:
        if not self.contains(handle):
            return False
        if handle is not None and not handle.is_cancelled:
            handle.cancel()
            return True
        return False

    def cancel_all(self) -> None:
        for handle in list(self._tasks.values()):
            if not handle.is_cancelled:
                handle.cancel()

    def cancel_matching(self, predicate) -> int:
        """Cancel active tasks matching ``predicate(handle)``. Returns count cancelled."""
        cancelled = 0
        for handle in list(self._tasks.values()):
            if handle.is_cancelled:
                continue
            try:
                if predicate(handle):
                    handle.cancel()
                    cancelled += 1
            except Exception:
                continue
        return cancelled

    def active_count(self, *, action: str | None = None) -> int:
        if action is None:
            return len(self._tasks)
        return sum(1 for handle in self._tasks.values() if handle.action == action)

    def _route_progress(
        self,
        task_id: str,
        message: object,
        callback: ProgressCallback,
    ) -> None:
        if task_id in self._tasks:
            callback(message)

    def _finish_done(
        self,
        task_id: str,
        emitted_action: str,
        result: object,
        callback: DoneCallback,
    ) -> None:
        handle = self._tasks.pop(task_id, None)
        if handle is None:
            return
        # Deliver the result even on an unexpected action mismatch — dropping it
        # would leave UI stuck in a "busy" state with no way to recover.
        callback(result)

    def _finish_failed(
        self,
        task_id: str,
        exc: object,
        callback: FailedCallback,
    ) -> None:
        if self._tasks.pop(task_id, None) is None:
            return
        callback(exc)
