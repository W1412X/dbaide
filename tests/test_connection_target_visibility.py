"""The desktop must dial, and report, the address the user actually configured.

Regression cover for the Windows report where a connection saved with one host failed
against a different address: the long-lived shared daemon served a cached config, and
the error text never said which server it had dialed.
"""

from __future__ import annotations

import pytest

from dbaide.config import ConfigManager
from dbaide.desktop import service as service_module
from dbaide.desktop.service import DesktopService
from dbaide.desktop.service_payloads import connection_payload, connection_target
from dbaide.models import ConnectionConfig


def _service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_ASSETS", str(tmp_path / "assets"))
    return DesktopService(ConfigManager(tmp_path / "config.toml"))


def test_connection_target_names_the_dialed_address():
    conn = ConnectionConfig(name="prod", type="mysql", host="112.6.118.232", port=3307,
                            user="u", database="shop")
    assert connection_target(conn) == "112.6.118.232:3307/shop"


def test_connection_target_without_host_or_port():
    conn = ConnectionConfig(name="local", type="postgres", user="u")
    assert connection_target(conn) == "localhost/"
    assert connection_target(ConnectionConfig(name="s", type="sqlite", path="/tmp/s.db")) == "/tmp/s.db"


def test_saved_payload_echoes_the_persisted_target(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    result = svc.dispatch("save_connection", {
        "name": "prod", "type": "mysql", "host": "112.6.118.232", "port": 3307,
        "user": "u", "database": "shop", "password": "pw",
    })
    assert result["connection"]["target"] == "112.6.118.232:3307/shop"
    # The GUI renders this echo, so it can never display a host the daemon did not store.
    assert result["connection"]["host"] == svc.cfg.get_connection("prod").host


def test_test_connection_failure_reports_the_dialed_target(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)

    class _Failing:
        def test(self):
            raise RuntimeError("(2003, \"Can't connect to MySQL server on '10.0.0.1'\")")

    monkeypatch.setattr(service_module, "build_adapter", lambda conn, **_kw: _Failing())
    with pytest.raises(RuntimeError) as exc:
        svc.dispatch("test_connection", {
            "name": "prod", "type": "mysql", "host": "112.6.118.232", "port": 3306,
            "user": "u", "database": "shop",
        })
    message = str(exc.value)
    assert "prod -> 112.6.118.232:3306/shop" in message
    assert "Can't connect" in message


def test_connection_payload_target_uses_the_same_format():
    conn = ConnectionConfig(name="prod", type="postgres", host="10.0.0.9", port=5432,
                            user="u", database="analytics")
    assert connection_payload(conn, has_assets=False)["target"] == "10.0.0.9:5432/analytics"
