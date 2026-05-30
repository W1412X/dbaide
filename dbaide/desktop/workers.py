from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from dbaide.desktop.service import DesktopService


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

    def run(self) -> None:
        try:
            if self.action in ("build_assets", "ask") and "progress" not in self.payload:
                self.payload["progress"] = self.signals.progress.emit
            result = self.service.dispatch(self.action, self.payload)
            self.signals.done.emit(self.action, result)
        except Exception as exc:
            self.signals.failed.emit(exc)
