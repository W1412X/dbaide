"""Single-process local application backend for DBAide desktop and MCP.

The daemon owns DesktopService, the MCP runtime, adapter caches, the SQL governor,
and physical database connection pools. The Qt GUI and MCP clients connect over
authenticated loopback transports instead of owning separate business runtimes.
"""

from __future__ import annotations

import hmac
import json
import os
import plistlib
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, is_dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dbaide.config import DEFAULT_CONFIG_DIR
from dbaide.desktop.service_actions import STREAMING_ACTIONS


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19742
MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
DESKTOP_RPC_PATH = "/desktop/rpc"
DESKTOP_CANCEL_PATH = "/desktop/cancel"
DESKTOP_RPC_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60
VALID_MODES = frozenset({"full", "ask", "tools"})
SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
})


def _json_safe(value: Any) -> Any:
    """Convert desktop-service payloads to transport-safe JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def runtime_dir(config_dir: Path | None = None) -> Path:
    return Path(config_dir or (DEFAULT_CONFIG_DIR / "mcp"))


def token_path(config_dir: Path | None = None) -> Path:
    return runtime_dir(config_dir) / "token"


def pid_path(config_dir: Path | None = None) -> Path:
    return runtime_dir(config_dir) / "daemon.pid"


def log_path(config_dir: Path | None = None) -> Path:
    return runtime_dir(config_dir) / "daemon.log"


def _secure_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_or_create_token(config_dir: Path | None = None) -> str:
    path = token_path(config_dir)
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    _secure_write(path, value + "\n")
    return value


def daemon_port() -> int:
    raw = os.environ.get("DBAIDE_MCP_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("DBAIDE_MCP_PORT must be an integer") from exc
    if not (1 <= port <= 65535):
        raise ValueError("DBAIDE_MCP_PORT must be between 1 and 65535")
    return port


def autostart_enabled() -> bool:
    """Return whether DBAide should own the daemon lifecycle."""
    return os.environ.get("DBAIDE_MCP_AUTOSTART", "1").strip().lower() not in {
        "0", "false", "no",
    }


def endpoint(*, mode: str = "full", host: str = DEFAULT_HOST, port: int | None = None) -> str:
    safe_mode = mode if mode in VALID_MODES else "full"
    return f"http://{host}:{port or daemon_port()}{MCP_PATH}?mode={safe_mode}"


def auth_headers(config_dir: Path | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {load_or_create_token(config_dir)}"}


def _health_url(*, host: str = DEFAULT_HOST, port: int | None = None) -> str:
    return f"http://{host}:{port or daemon_port()}{HEALTH_PATH}"


def _daemon_health(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    config_dir: Path | None = None,
    timeout: float = 0.35,
) -> dict[str, Any] | None:
    request = urllib.request.Request(_health_url(host=host, port=port), headers=auth_headers(config_dir))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != HTTPStatus.OK:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if payload.get("name") == "dbaide-mcp" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def is_daemon_running(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    config_dir: Path | None = None,
    timeout: float = 0.35,
    require_desktop: bool = True,
) -> bool:
    payload = _daemon_health(
        host=host,
        port=port,
        config_dir=config_dir,
        timeout=timeout,
    )
    if payload is None:
        return False
    return not require_desktop or payload.get("desktopRpcVersion") == DESKTOP_RPC_VERSION


def _daemon_command(*, host: str, port: int) -> list[str]:
    args = ["mcp-daemon", "run", "--host", host, "--port", str(port)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "dbaide.cli", *args]


def ensure_daemon(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    config_dir: Path | None = None,
    timeout: float = 5.0,
) -> str:
    """Start the shared daemon when needed and return its MCP endpoint."""
    actual_port = port or daemon_port()
    load_or_create_token(config_dir)
    health = _daemon_health(host=host, port=actual_port, config_dir=config_dir)
    if health and health.get("desktopRpcVersion") == DESKTOP_RPC_VERSION:
        return endpoint(host=host, port=actual_port)
    if health:
        # An older DBAide daemon can survive an application update. Stop it
        # before binding the upgraded desktop-capable service to the same port.
        if not stop_daemon(config_dir=config_dir):
            raise RuntimeError(
                "An incompatible DBAide MCP daemon is running and could not be stopped"
            )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if _daemon_health(host=host, port=actual_port, config_dir=config_dir) is None:
                break
            time.sleep(0.05)

    directory = runtime_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path(config_dir), "ab", buffering=0)  # noqa: SIM115
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(_daemon_command(host=host, port=actual_port), **kwargs)
    finally:
        log_file.close()

    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        if is_daemon_running(host=host, port=actual_port, config_dir=config_dir):
            return endpoint(host=host, port=actual_port)
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise RuntimeError(
        f"DBAide MCP daemon failed to start on {host}:{actual_port}; "
        f"see {log_path(config_dir)}"
    )


def install_daemon_autostart(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    config_dir: Path | None = None,
    launch_agents_dir: Path | None = None,
) -> Path | None:
    """Install the per-user boot entry where the platform supports it.

    macOS LaunchAgents are intentionally installed without bootstrapping them
    into the current login session: ``ensure_daemon`` starts the daemon now,
    while launchd owns it after the next login. This avoids racing two owners
    for the same loopback port during installation.
    """
    if sys.platform != "darwin":
        return None
    actual_port = port or daemon_port()
    directory = runtime_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    agents = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    path = agents / "dev.dbaide.mcp.plist"
    environment: dict[str, str] = {}
    configured = os.environ.get("DBAIDE_CONFIG", "").strip()
    if configured:
        environment["DBAIDE_CONFIG"] = configured
    if os.environ.get("DBAIDE_MCP_PORT", "").strip():
        environment["DBAIDE_MCP_PORT"] = str(actual_port)
    payload: dict[str, Any] = {
        "Label": "dev.dbaide.mcp",
        "ProgramArguments": _daemon_command(host=host, port=actual_port),
        "RunAtLoad": True,
        # Restart crashes, but not a clean `mcp-daemon stop` (which exits 0).
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 3,
        "ProcessType": "Background",
        "StandardOutPath": str(log_path(config_dir)),
        "StandardErrorPath": str(log_path(config_dir)),
    }
    if environment:
        payload["EnvironmentVariables"] = environment
    xml = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")
    _secure_write(path, xml)
    return path


def stop_daemon(*, config_dir: Path | None = None, timeout: float = 3.0) -> bool:
    path = pid_path(config_dir)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            path.unlink(missing_ok=True)
            return True
        time.sleep(0.05)
    return True


class _MCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        token: str,
        config_dir: Path,
        config_path: Path | None = None,
    ):
        super().__init__(address, _MCPRequestHandler)
        self.auth_token = token
        self.config_dir = config_dir
        self.config_path = Path(config_path or (config_dir.parent / "config.toml"))
        self._sessions: dict[str, float] = {}
        self._sessions_lock = threading.Lock()
        self._desktop_service_instance: Any | None = None
        self._desktop_service_lock = threading.Lock()
        self._desktop_requests: dict[str, threading.Event] = {}
        self._desktop_early_cancellations: dict[str, float] = {}
        self._desktop_requests_lock = threading.Lock()

    def create_session(self) -> str:
        now = time.monotonic()
        session_id = secrets.token_urlsafe(24)
        with self._sessions_lock:
            self._sessions = {
                key: seen
                for key, seen in self._sessions.items()
                if now - seen <= SESSION_TTL_SECONDS
            }
            self._sessions[session_id] = now
        return session_id

    def touch_session(self, session_id: str) -> bool:
        if not session_id:
            return False
        now = time.monotonic()
        with self._sessions_lock:
            seen = self._sessions.get(session_id)
            if seen is None or now - seen > SESSION_TTL_SECONDS:
                self._sessions.pop(session_id, None)
                return False
            self._sessions[session_id] = now
            return True

    def delete_session(self, session_id: str) -> bool:
        with self._sessions_lock:
            return self._sessions.pop(session_id, None) is not None

    def desktop_service(self) -> Any:
        """Return the one DesktopService owned by this daemon process."""
        if self._desktop_service_instance is None:
            with self._desktop_service_lock:
                if self._desktop_service_instance is None:
                    from dbaide.config import ConfigManager
                    from dbaide.desktop.service import DesktopService
                    from dbaide.agent.llm_trace import set_tracing

                    config = ConfigManager(self.config_path)
                    # Tracing is process-local and the daemon, not the Qt client,
                    # executes workflows. Initialize it once with the daemon service.
                    set_tracing(config.debug_trace())
                    self._desktop_service_instance = DesktopService(config)
        return self._desktop_service_instance

    def begin_desktop_request(self, request_id: str) -> threading.Event:
        event = threading.Event()
        with self._desktop_requests_lock:
            if request_id in self._desktop_requests:
                raise ValueError("duplicate desktop request id")
            now = time.monotonic()
            self._desktop_early_cancellations = {
                key: timestamp
                for key, timestamp in self._desktop_early_cancellations.items()
                if now - timestamp <= 5.0
            }
            if self._desktop_early_cancellations.pop(request_id, None) is not None:
                event.set()
            self._desktop_requests[request_id] = event
        return event

    def finish_desktop_request(self, request_id: str) -> None:
        with self._desktop_requests_lock:
            self._desktop_requests.pop(request_id, None)

    def cancel_desktop_request(self, request_id: str) -> bool:
        with self._desktop_requests_lock:
            event = self._desktop_requests.get(request_id)
            if event is None:
                # A Qt worker may be cancelled just before its POST reaches the
                # daemon. Remember it briefly so that race cannot start the task.
                self._desktop_early_cancellations[request_id] = time.monotonic()
                return True
        event.set()
        return True


class _MCPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DBAideMCP/1"

    @property
    def mcp_server(self) -> _MCPHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        # The daemon process already redirects stderr to its lifecycle log.
        print(f"mcp-http: {self.address_string()} - {fmt % args}", file=sys.stderr)

    def _json_response(
        self,
        status: int,
        payload: Any | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme in ("http", "https") and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.mcp_server.auth_token}"
        return hmac.compare_digest(supplied, expected)

    def _preflight(self) -> bool:
        if not self._allowed_origin():
            self._json_response(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
            return False
        if not self._authorized():
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Bearer realm="dbaide-mcp"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def _read_json_body(self) -> Any:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise TypeError("application/json required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid content length")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON") from exc

    def _write_ndjson_chunk(self, payload: dict[str, Any], lock: threading.Lock) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False).encode("utf-8") + b"\n"
        with lock:
            self.wfile.write(f"{len(body):X}\r\n".encode("ascii"))
            self.wfile.write(body)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

    def _handle_desktop_cancel(self) -> None:
        if not self._preflight():
            return
        try:
            body = self._read_json_body()
        except TypeError as exc:
            self._json_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": str(exc)})
            return
        except ValueError as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        request_id = str(body.get("request_id") or "") if isinstance(body, dict) else ""
        if not request_id:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "request_id required"})
            return
        cancelled = self.mcp_server.cancel_desktop_request(request_id)
        self._json_response(HTTPStatus.OK, {"cancelled": cancelled})

    def _handle_desktop_rpc(self) -> None:
        if not self._preflight():
            return
        try:
            body = self._read_json_body()
        except TypeError as exc:
            self._json_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": str(exc)})
            return
        except ValueError as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if not isinstance(body, dict):
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "request object required"})
            return
        action = str(body.get("action") or "").strip()
        payload = body.get("payload") or {}
        request_id = str(body.get("request_id") or secrets.token_urlsafe(18))
        if not action or not isinstance(payload, dict):
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "action and object payload required"})
            return
        try:
            cancel_event = self.mcp_server.begin_desktop_request(request_id)
        except ValueError as exc:
            self._json_response(HTTPStatus.CONFLICT, {"error": str(exc)})
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        write_lock = threading.Lock()

        from dbaide.core.cancellation import CancelledError

        def check_cancelled() -> None:
            if cancel_event.is_set():
                raise CancelledError("Task cancelled by user")

        def progress(value: Any) -> None:
            check_cancelled()
            try:
                self._write_ndjson_chunk({"type": "progress", "value": value}, write_lock)
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                cancel_event.set()
                raise CancelledError("Desktop client disconnected") from exc

        server_payload = dict(payload)
        if action in STREAMING_ACTIONS:
            server_payload["progress"] = progress
            server_payload["cancel_check"] = check_cancelled
        try:
            check_cancelled()
            result = self.mcp_server.desktop_service().dispatch(action, server_payload)
            check_cancelled()
            self._write_ndjson_chunk({"type": "result", "value": result}, write_lock)
        except (BrokenPipeError, ConnectionResetError, OSError):
            cancel_event.set()
        except Exception as exc:  # noqa: BLE001
            try:
                self._write_ndjson_chunk({
                    "type": "error",
                    "error": {"class": type(exc).__name__, "message": str(exc)},
                }, write_lock)
            except (BrokenPipeError, ConnectionResetError, OSError):
                cancel_event.set()
        finally:
            self.mcp_server.finish_desktop_request(request_id)
            try:
                with write_lock:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == HEALTH_PATH:
            if not self._preflight():
                return
            self._json_response(HTTPStatus.OK, {
                "name": "dbaide-mcp",
                "pid": os.getpid(),
                "desktopRpcVersion": DESKTOP_RPC_VERSION,
                # Release smoke tests use this to prove that the frozen daemon
                # stayed on the headless path on macOS, Linux, and Windows.
                # PyInstaller's runtime hook imports PyQt6.QtCore to relocate
                # Qt's plugin paths even for CLI commands. Loading any window,
                # rendering, or WebEngine module means the daemon actually
                # crossed into the GUI runtime and could create desktop UI.
                "uiRuntimeLoaded": any(name in sys.modules for name in (
                    "PyQt6.QtGui",
                    "PyQt6.QtWidgets",
                    "PyQt6.QtWebEngineCore",
                    "PyQt6.QtWebEngineWidgets",
                )),
            })
            return
        if parsed.path == MCP_PATH:
            if not self._preflight():
                return
            session_id = self.headers.get("MCP-Session-Id", "")
            if not self.mcp_server.touch_session(session_id):
                status = HTTPStatus.NOT_FOUND if session_id else HTTPStatus.BAD_REQUEST
                self._json_response(status, {"error": "valid MCP session required"})
                return
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if urlparse(self.path).path != MCP_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._preflight():
            return
        session_id = self.headers.get("MCP-Session-Id", "")
        if not self.mcp_server.delete_session(session_id):
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "session not found"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == DESKTOP_RPC_PATH:
            self._handle_desktop_rpc()
            return
        if parsed.path == DESKTOP_CANCEL_PATH:
            self._handle_desktop_cancel()
            return
        if parsed.path != MCP_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._preflight():
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._json_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        requested_version = self.headers.get("MCP-Protocol-Version", "").strip()
        if requested_version and requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "unsupported MCP protocol version"})
            return
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(HTTPStatus.BAD_REQUEST, {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            })
            return
        if not isinstance(message, dict):
            self._json_response(HTTPStatus.BAD_REQUEST, {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            })
            return

        is_initialize = message.get("method") == "initialize" and message.get("id") is not None
        if is_initialize:
            session_id = self.mcp_server.create_session()
        else:
            session_id = self.headers.get("MCP-Session-Id", "")
            if not self.mcp_server.touch_session(session_id):
                status = HTTPStatus.NOT_FOUND if session_id else HTTPStatus.BAD_REQUEST
                self._json_response(status, {"error": "valid MCP session required"})
                return

        from dbaide.mcp_server import handle_transport_message

        mode = (parse_qs(parsed.query).get("mode") or ["full"])[0]
        mode = mode if mode in VALID_MODES else "full"
        outbound: list[Any] = []
        response = handle_transport_message(
            message,
            mode=mode,
            sender=outbound.append,
            session_id=session_id,
        )

        # Notifications and client responses are accepted without a response body.
        if message.get("id") is None:
            self._json_response(HTTPStatus.ACCEPTED)
            return
        if response is None:
            self._json_response(HTTPStatus.ACCEPTED)
            return
        if outbound:
            events = [*outbound, response]
            body = "".join(
                f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                for event in events
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Content-Length", str(len(body)))
            if is_initialize:
                self.send_header("MCP-Session-Id", session_id)
            self.end_headers()
            self.wfile.write(body)
            return
        response_headers = {"MCP-Session-Id": session_id} if is_initialize else None
        self._json_response(HTTPStatus.OK, response, headers=response_headers)


def serve_http(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    config_dir: Path | None = None,
    config_path: Path | None = None,
) -> None:
    """Run the shared loopback-only MCP daemon until terminated."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the local DBAide MCP daemon may only bind to loopback")
    actual_port = port or daemon_port()
    directory = runtime_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    token = load_or_create_token(config_dir)
    server = _MCPHTTPServer(
        (host, actual_port),
        token=token,
        config_dir=directory,
        config_path=config_path,
    )
    _secure_write(pid_path(config_dir), f"{os.getpid()}\n")

    def shutdown(_signum: int, _frame: Any) -> None:
        # shutdown() must run on a different thread from serve_forever().
        threading.Thread(target=server.shutdown, daemon=True).start()

    old_term = signal.signal(signal.SIGTERM, shutdown)
    old_int = signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        try:
            if pid_path(config_dir).read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path(config_dir).unlink(missing_ok=True)
        except OSError:
            pass
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
