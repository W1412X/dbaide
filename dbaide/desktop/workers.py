from __future__ import annotations

import threading
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from dbaide.core.cancellation import CancelledError
from dbaide.desktop.service import DesktopService


class WorkerSignals(QObject):
    progress = pyqtSignal(object)
    done = pyqtSignal(str, object)
    failed = pyqtSignal(object)


class ServiceWorker(QRunnable):
    _live: set["ServiceWorker"] = set()
    _live_lock = threading.Lock()

    def __init__(self, service: DesktopService, action: str, payload: dict[str, Any]) -> None:
        super().__init__()
        # The Python side owns WorkerSignals and callback connections. Letting Qt
        # auto-delete the QRunnable while Python slots are still queued is fragile
        # during rapid test/window teardown, so ownership stays explicit.
        self.setAutoDelete(False)
        self.service = service
        self.action = action
        self.payload = dict(payload)
        self.signals = WorkerSignals()
        self._cancelled = False
        with self._live_lock:
            self._live.add(self)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError("Task cancelled by user")

    def _emit_progress(self, message: str | dict) -> None:
        self._check_cancelled()
        self._safe_emit("progress", message)

    def _safe_emit(self, signal_name: str, *args: object) -> None:
        try:
            signals = self.signals
            if sip.isdeleted(signals):
                return
            getattr(signals, signal_name).emit(*args)
        except RuntimeError:
            # The owning window may have been torn down while the pool thread was
            # finishing. Dropping a late UI signal is safer than aborting Python.
            pass

    def run(self) -> None:
        try:
            if self.action in (
                "build_assets", "ask", "project_instance", "refresh_instance", "enrich_table",
                "execute_sql", "explain_sql", "browse_table", "count_table", "backup_run",
            ):
                self.payload.setdefault("progress", self._emit_progress)
                self.payload.setdefault("cancel_check", self._check_cancelled)
            result = self.service.dispatch(self.action, self.payload)
            self._check_cancelled()
            self._safe_emit("done", self.action, result)
        except CancelledError as exc:
            self._safe_emit("failed", exc)
        except Exception as exc:
            self._safe_emit("failed", exc)
        finally:
            with self._live_lock:
                self._live.discard(self)
