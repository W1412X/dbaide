from __future__ import annotations

import json
import plistlib
import socket
import socketserver
import stat
import threading
import urllib.error
import urllib.request

import pytest

from dbaide import mcp_http


@pytest.fixture
def http_server(tmp_path):
    token = mcp_http.load_or_create_token(tmp_path)
    server = mcp_http._MCPHTTPServer(("127.0.0.1", 0), token=token, config_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(base: str, token: str, path: str, *, payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode()
    merged = {"Authorization": f"Bearer {token}", **(headers or {})}
    if body is not None:
        merged.setdefault("Content-Type", "application/json")
        merged.setdefault("Accept", "application/json, text/event-stream")
    return urllib.request.Request(base + path, data=body, headers=merged)


def _initialize(base: str, token: str, *, mode: str = "full") -> tuple[str, dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }
    with urllib.request.urlopen(_request(base, token, f"/mcp?mode={mode}", payload=payload)) as response:
        return response.headers["MCP-Session-Id"], json.loads(response.read())


def test_token_is_persistent_and_private(tmp_path):
    first = mcp_http.load_or_create_token(tmp_path)
    second = mcp_http.load_or_create_token(tmp_path)
    assert first == second
    assert len(first) >= 32
    if hasattr(stat, "S_IMODE"):
        assert stat.S_IMODE(mcp_http.token_path(tmp_path).stat().st_mode) & 0o077 == 0


@pytest.mark.parametrize("value", ["0", "false", "NO"])
def test_autostart_can_be_delegated_to_an_external_supervisor(monkeypatch, value):
    monkeypatch.setenv("DBAIDE_MCP_AUTOSTART", value)
    assert not mcp_http.autostart_enabled()


def test_macos_autostart_runs_one_shared_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_http.sys, "platform", "darwin")
    agents = tmp_path / "LaunchAgents"
    path = mcp_http.install_daemon_autostart(
        port=23456,
        config_dir=tmp_path / "runtime",
        launch_agents_dir=agents,
    )
    assert path == agents / "dev.dbaide.mcp.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == "dev.dbaide.mcp"
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["ProgramArguments"][-4:] == ["--host", "127.0.0.1", "--port", "23456"]


def test_health_requires_bearer_auth(http_server):
    base, token = http_server
    with urllib.request.urlopen(_request(base, token, "/health")) as response:
        assert response.status == 200
        health = json.loads(response.read())
        assert health["name"] == "dbaide-mcp"
        assert isinstance(health["uiRuntimeLoaded"], bool)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + "/health")
    assert exc.value.code == 401


def test_initialize_and_mode_scoped_tool_listing(http_server):
    base, token = http_server
    tools_session, payload = _initialize(base, token, mode="tools")
    assert payload["result"]["protocolVersion"] == "2025-11-25"

    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    with urllib.request.urlopen(_request(
        base,
        token,
        "/mcp?mode=tools",
        payload=listing,
        headers={"MCP-Session-Id": tools_session},
    )) as response:
        names = {tool["name"] for tool in json.loads(response.read())["result"]["tools"]}
        assert "execute_sql" in names
        assert "ask" not in names
    ask_session, _ = _initialize(base, token, mode="ask")
    with urllib.request.urlopen(_request(
        base,
        token,
        "/mcp?mode=ask",
        payload=listing,
        headers={"MCP-Session-Id": ask_session},
    )) as response:
        names = {tool["name"] for tool in json.loads(response.read())["result"]["tools"]}
        assert names == {"ask"}


def test_notification_returns_202_and_get_mcp_returns_405(http_server):
    base, token = http_server
    session_id, _ = _initialize(base, token)
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    session_header = {"MCP-Session-Id": session_id}
    with urllib.request.urlopen(_request(
        base, token, "/mcp", payload=notification, headers=session_header,
    )) as response:
        assert response.status == 202
        assert response.read() == b""
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(base, token, "/mcp", headers=session_header))
    assert exc.value.code == 405


def test_session_is_required_and_can_be_deleted(http_server):
    base, token = http_server
    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(base, token, "/mcp", payload=ping))
    assert exc.value.code == 400

    session_id, _ = _initialize(base, token)
    delete = urllib.request.Request(
        base + "/mcp",
        method="DELETE",
        headers={
            "Authorization": f"Bearer {token}",
            "MCP-Session-Id": session_id,
        },
    )
    with urllib.request.urlopen(delete) as response:
        assert response.status == 204
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(
            base,
            token,
            "/mcp",
            payload=ping,
            headers={"MCP-Session-Id": session_id},
        ))
    assert exc.value.code == 404


def test_rejects_non_loopback_origin_and_unknown_protocol(http_server):
    base, token = http_server
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(
            base,
            token,
            "/mcp",
            payload=ping,
            headers={"Origin": "https://evil.example"},
        ))
    assert exc.value.code == 403

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(
            base,
            token,
            "/mcp",
            payload=ping,
            headers={"MCP-Protocol-Version": "2099-01-01"},
        ))
    assert exc.value.code == 400


def test_outbound_notifications_use_sse_response(http_server, monkeypatch):
    base, token = http_server
    session_id, _ = _initialize(base, token)

    def fake_handle(message, *, mode, sender, session_id):
        sender({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}

    monkeypatch.setattr("dbaide.mcp_server.handle_transport_message", fake_handle)
    request = {"jsonrpc": "2.0", "id": 9, "method": "ping"}
    with urllib.request.urlopen(_request(
        base,
        token,
        "/mcp",
        payload=request,
        headers={"MCP-Session-Id": session_id},
    )) as response:
        body = response.read().decode()
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "notifications/progress" in body
        assert '"id": 9' in body


def _serve_loopback_handler():
    """A stand-in for the daemon: a plain loopback HTTP server on an ephemeral port."""
    from http.server import BaseHTTPRequestHandler

    class _Ok(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            pass

    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_direct_urlopen_ignores_the_system_proxy(monkeypatch):
    """A Windows registry / Clash-style proxy must not intercept GUI -> daemon calls.

    Otherwise every health probe fails, each GUI launch spawns another daemon, and the
    desktop ends up talking to several backends holding different configurations.
    """
    server, thread = _serve_loopback_handler()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/health"
        dead = f"http://127.0.0.1:{server.server_address[1] + 1}"
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": dead})
        with pytest.raises(urllib.error.URLError):
            urllib.request.build_opener().open(url, timeout=2)

        def _boom():
            raise AssertionError("the loopback channel must not consult proxy configuration")

        monkeypatch.setattr(urllib.request, "getproxies", _boom)
        monkeypatch.setattr(mcp_http, "_DIRECT_OPENER", None)   # force a fresh opener
        with mcp_http.direct_urlopen(urllib.request.Request(url), timeout=2) as response:
            assert response.read() == b"ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
class _RecordingSocket:
    """A listening socket stub that records the options requested before bind()."""

    def __init__(self) -> None:
        self.options: list[int] = []

    def setsockopt(self, _level: int, option: int, _value: int) -> None:
        self.options.append(option)

    def bind(self, _address) -> None:
        pass

    def getsockname(self):
        return ("127.0.0.1", 0)


def _bind_daemon_socket(monkeypatch, *, windows: bool) -> list[int]:
    """Run the daemon's server_bind with stubbed platform views.

    ``mcp_http.os``/``mcp_http.socket`` are replaced instead of patched in place: the
    real ``os.name`` is process-global, and flipping it to "nt" breaks pytest's own
    pathlib while it reports the failure.
    """
    from types import SimpleNamespace

    exclusive = -15
    sock = _RecordingSocket()
    stub_socket = SimpleNamespace(
        SOL_SOCKET=0x0FFF,
        **({"SO_EXCLUSIVEADDRUSE": exclusive} if windows else {}),
    )

    def _tcp_server_bind(self):
        # What socketserver does — after the subclass override has already run.
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    monkeypatch.setattr(mcp_http, "os", SimpleNamespace(name="nt" if windows else "posix"))
    monkeypatch.setattr(mcp_http, "socket", stub_socket)
    monkeypatch.setattr(socketserver.TCPServer, "server_bind", _tcp_server_bind)

    server = object.__new__(mcp_http._MCPHTTPServer)
    server.socket = sock
    server.server_address = ("127.0.0.1", 0)
    mcp_http._MCPHTTPServer.server_bind(server)
    return sock.options


def test_windows_bind_is_exclusive_and_stops_requesting_reuse(monkeypatch):
    """A second listener must not be able to share the port — and SO_REUSEADDR is off.

    Windows answers WSAEINVAL (10022) for SO_REUSEADDR on a socket already marked
    SO_EXCLUSIVEADDRUSE, and socketserver applies that flag after server_bind, so the
    0.9.29 Windows build died at daemon startup until exclusivity switched reuse off.
    """
    options = _bind_daemon_socket(monkeypatch, windows=True)
    assert options == [-15]
    assert socket.SO_REUSEADDR not in options


def test_posix_bind_keeps_reusing_the_loopback_port(monkeypatch):
    options = _bind_daemon_socket(monkeypatch, windows=False)
    assert options == [socket.SO_REUSEADDR]
