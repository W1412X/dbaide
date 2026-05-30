from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from dbaide.desktop.service import DesktopService


class CancelledError(Exception):
    """Raised when the user cancels an in-flight desktop worker task."""


class WorkerSignals(QObject):
    progress = pyqtSignal(str)
    done = pyqtSignal(str, object)
    failed = pyqtSignal(object)


class ServiceWorker(QRunnable):
    def __init__(self, service: DesktopService, action: str, payload: dict[str, Any]) -> None:
        super().__init__()
        self.service = service
        self.action = action
        self.payload = dict(payload)
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError("Task cancelled by user")

    def _emit_progress(self, message: str) -> None:
        self._check_cancelled()
        self.signals.progress.emit(message)

    def run(self) -> None:
        try:
            if self.action in ("build_assets", "ask"):
                self.payload.setdefault("progress", self._emit_progress)
                self.payload.setdefault("cancel_check", self._check_cancelled)
            result = self.service.dispatch(self.action, self.payload)
            self._check_cancelled()
            self.signals.done.emit(self.action, result)
        except CancelledError as exc:
            self.signals.failed.emit(exc)
        except Exception as exc:
            self.signals.failed.emit(exc)
