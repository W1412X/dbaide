"""DesktopService-compatible client for the shared DBAide backend.

The Qt process keeps rendering and worker scheduling locally, while every
business action executes inside the one long-running daemon process.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from dbaide.core.cancellation import CancelledError
from dbaide.mcp_http import (
    DEFAULT_HOST,
    DESKTOP_CANCEL_PATH,
    DESKTOP_RPC_PATH,
    auth_headers,
    daemon_port,
)


class RemoteServiceError(RuntimeError):
    """A desktop action failed in the shared backend."""


class RemoteServiceUnavailable(RemoteServiceError):
    """The local shared backend could not be reached."""


class RemoteDesktopService:
    """Duck-typed replacement for :class:`DesktopService` used by the GUI."""

    def __init__(
        self,
        *,
        config_dir: Path,
        host: str = DEFAULT_HOST,
        port: int | None = None,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.host = host
        self.port = port or daemon_port()
        self.base_url = f"http://{self.host}:{self.port}"
        self._headers = {
            **auth_headers(self.config_dir),
            "Content-Type": "application/json",
        }

    def dispatch(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        return self.dispatch_with_request(
            action,
            payload or {},
            request_id=f"desktop:{uuid4().hex}",
        )

    def dispatch_with_request(
        self,
        action: str,
        payload: dict[str, Any] | None,
        *,
        request_id: str,
    ) -> Any:
        source = dict(payload or {})
        progress = source.pop("progress", None)
        source.pop("cancel_check", None)
        request = urllib.request.Request(
            self.base_url + DESKTOP_RPC_PATH,
            data=json.dumps({
                "request_id": request_id,
                "action": action,
                "payload": source,
            }, ensure_ascii=False).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=None) as response:
                result_seen = False
                result: Any = None
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    event = json.loads(raw_line.decode("utf-8"))
                    kind = event.get("type") if isinstance(event, dict) else ""
                    if kind == "progress":
                        if callable(progress):
                            progress(event.get("value"))
                    elif kind == "result":
                        result = event.get("value")
                        result_seen = True
                    elif kind == "error":
                        self._raise_remote_error(event.get("error") or {})
                if not result_seen:
                    raise RemoteServiceError(
                        f"Shared backend ended '{action}' without a result"
                    )
                return result
        except CancelledError:
            raise
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except OSError:
                detail = ""
            raise RemoteServiceError(
                f"Shared backend rejected '{action}' ({exc.code}): {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, ConnectionError, socket.timeout, OSError) as exc:
            raise RemoteServiceUnavailable(
                f"Shared DBAide backend is unavailable at {self.base_url}: {exc}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteServiceError(
                f"Shared backend returned an invalid response for '{action}'"
            ) from exc

    def cancel_request(self, request_id: str) -> bool:
        request = urllib.request.Request(
            self.base_url + DESKTOP_CANCEL_PATH,
            data=json.dumps({"request_id": request_id}).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return bool(payload.get("cancelled"))
        except (OSError, ValueError, urllib.error.URLError):
            return False

    @staticmethod
    def _raise_remote_error(error: dict[str, Any]) -> None:
        message = str(error.get("message") or "Desktop action failed")
        class_name = str(error.get("class") or "")
        if class_name == "CancelledError":
            raise CancelledError(message)
        if class_name == "ValueError":
            raise ValueError(message)
        if class_name == "KeyError":
            raise KeyError(message)
        raise RemoteServiceError(message)

