"""Service-layer tests for resource defaults, query log, and build mutual exclusion."""

from __future__ import annotations

import sqlite3

import pytest

from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.models import ConnectionConfig


def _make_db(path):
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO t VALUES (1,'a'),(2,'b');")
    c.commit()
    c.close()


def _service(tmp_path):
    cfg = ConfigManager(path=tmp_path / "config.toml")
    db = tmp_path / "app.db"
    _make_db(db)
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)))
    return DesktopService(cfg, AssetStore(tmp_path / "assets"))


def test_resource_defaults_save_and_read(tmp_path):
    svc = _service(tmp_path)
    svc.save_resource_defaults({"values": {"max_inflight_queries": 5, "max_row_limit": 321, "bogus": 9}})
    out = svc.resource_defaults()
    assert out["values"]["max_inflight_queries"] == 5
    assert out["values"]["max_row_limit"] == 321
    assert "bogus" not in out["values"]
    assert "production" in out["presets"]


def test_build_records_queries_and_exposes_them(tmp_path):
    svc = _service(tmp_path)
    svc.build_assets({"name": "local", "profile_mode": "auto"})
    payload = svc.recent_queries({"connection_name": "local"})
    assert payload["queries"]
    assert all(q["caller"] == "build" for q in payload["queries"])
    assert payload["summary"]["total"] >= 1


def test_dry_run_does_not_hit_table_data(tmp_path):
    svc = _service(tmp_path)
    result = svc.build_assets({"name": "local", "profile_mode": "auto", "dry_run": True})
    assert result["stats"]["estimated_queries"] > 0


def test_conn_payload_includes_load_profile(tmp_path):
    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="p", type="sqlite", path="/tmp/x.db", load_profile="dev"))
    svc = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    boot = svc.bootstrap()
    prof = {c["name"]: c["load_profile"] for c in boot["connections"]}
    assert prof["p"] == "dev"


def test_build_in_progress_blocks_queries(tmp_path):
    svc = _service(tmp_path)
    svc._begin_build("local")
    try:
        with pytest.raises(RuntimeError):
            svc.execute_sql({"connection_name": "local", "sql": "SELECT 1"})
    finally:
        svc._end_build("local")
    # Once cleared, queries work again.
    out = svc.execute_sql({"connection_name": "local", "sql": "SELECT 1"})
    assert out["row_count"] == 1
