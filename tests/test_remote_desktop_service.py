from __future__ import annotations

import threading
import time
import subprocess
import sys
from pathlib import Path

import pytest

from dbaide import mcp_http
from dbaide.core.cancellation import CancelledError
from dbaide.desktop.remote_service import RemoteDesktopService


def test_remote_client_import_does_not_load_desktop_service_implementation():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dbaide.desktop.remote_service; "
            "assert 'dbaide.desktop.service' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def remote_backend(tmp_path):
    token = mcp_http.load_or_create_token(tmp_path)
    server = mcp_http._MCPHTTPServer(
        ("127.0.0.1", 0),
        token=token,
        config_dir=tmp_path,
        config_path=tmp_path / "config.toml",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = RemoteDesktopService(
        config_dir=tmp_path,
        host="127.0.0.1",
        port=server.server_port,
    )
    try:
        yield client, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_remote_dispatch_streams_progress_and_serializes_result(remote_backend):
    client, server = remote_backend

    class FakeDesktopService:
        def __init__(self):
            self.calls = 0

        def dispatch(self, action, payload):
            self.calls += 1
            payload["progress"]({"stage": "working"})
            return {"action": action, "path": Path("/tmp/result"), "calls": self.calls}

    fake = FakeDesktopService()
    server._desktop_service_instance = fake
    progress = []
    result = client.dispatch("ask", {"question": "q", "progress": progress.append})

    assert progress == [{"stage": "working"}]
    assert result == {"action": "ask", "path": "/tmp/result", "calls": 1}
    assert client.dispatch("ask", {"progress": progress.append})["calls"] == 2
    assert server.desktop_service() is fake


def test_remote_request_can_be_cancelled_without_cross_request_leak(remote_backend):
    client, server = remote_backend
    started = threading.Event()

    class BlockingDesktopService:
        def dispatch(self, _action, payload):
            started.set()
            while True:
                payload["cancel_check"]()
                time.sleep(0.01)

    server._desktop_service_instance = BlockingDesktopService()
    outcome = []

    def run():
        try:
            client.dispatch_with_request("ask", {}, request_id="request-a")
        except Exception as exc:  # noqa: BLE001
            outcome.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(2)
    assert client.cancel_request("request-a")
    thread.join(timeout=2)

    assert len(outcome) == 1
    assert isinstance(outcome[0], CancelledError)
    assert not server._desktop_requests


def test_cancel_arriving_before_rpc_is_not_lost(remote_backend):
    client, server = remote_backend

    class NeverRunService:
        def dispatch(self, _action, _payload):
            raise AssertionError("cancelled request must not execute")

    server._desktop_service_instance = NeverRunService()
    assert client.cancel_request("early-request")
    with pytest.raises(CancelledError):
        client.dispatch_with_request("ask", {}, request_id="early-request")


def test_main_window_bootstraps_through_remote_service(remote_backend, tmp_path):
    from PyQt6.QtWidgets import QApplication

    from dbaide.assets import AssetStore
    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.desktop.views.main_window import MainWindow

    client, server = remote_backend
    server._desktop_service_instance = DesktopService(
        ConfigManager(tmp_path / "gui-config.toml"),
        AssetStore(tmp_path / "gui-assets"),
    )
    app = QApplication.instance() or QApplication([])
    window = MainWindow(client)
    deadline = time.monotonic() + 3
    while window.tasks.active_count() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    try:
        assert window.service is client
        assert not hasattr(client, "cfg")
        assert window.bootstrap.get("connections") == []
        assert window.tasks.active_count() == 0
    finally:
        window.close()
        app.processEvents()
