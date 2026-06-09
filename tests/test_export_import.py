"""Tests for connection/config export and import."""
from __future__ import annotations

import json

import pytest

from dbaide.config import ConfigManager
from dbaide.models import ConnectionConfig, ModelConfig
from dbaide.joins.catalog import JoinCatalogStore
from dbaide.annotations.store import AnnotationStore


def _make_service(tmp_path):
    """Build a minimal DesktopService with isolated storage."""
    cfg = ConfigManager(path=tmp_path / "config.toml")
    from dbaide.desktop.service import DesktopService
    svc = DesktopService.__new__(DesktopService)
    svc.cfg = cfg
    svc.join_catalog = JoinCatalogStore(base_dir=tmp_path / "joins")
    svc.annotations = AnnotationStore(base_dir=tmp_path / "annotations")
    return svc


class TestExportConnection:
    def test_export_basic(self, tmp_path):
        svc = _make_service(tmp_path)
        conn = ConnectionConfig(name="mydb", type="sqlite", path="/tmp/test.db")
        svc.cfg.upsert_connection(conn)

        result = svc.export_connection({"connection_name": "mydb"})
        assert result["dbaide_export"]["type"] == "connection"
        assert result["dbaide_export"]["version"] == 1
        assert result["connection"]["name"] == "mydb"
        assert result["connection"]["type"] == "sqlite"
        assert result["joins"] == []
        assert result["annotations"] == []

    def test_export_with_joins_and_annotations(self, tmp_path):
        svc = _make_service(tmp_path)
        conn = ConnectionConfig(name="prod", type="mysql", host="localhost", database="app")
        svc.cfg.upsert_connection(conn)

        svc.join_catalog.add("prod", {
            "table": "orders", "column": "user_id",
            "ref_table": "users", "ref_column": "id",
        }, source="user", database="app", fingerprint="fp1")

        svc.annotations.add("prod", scope="table", database="app",
                            table="users", note="Main user table")

        result = svc.export_connection({"connection_name": "prod"})
        assert len(result["joins"]) == 1
        assert result["joins"][0]["table"] == "orders"
        assert len(result["annotations"]) == 1
        assert result["annotations"][0]["note"] == "Main user table"

    def test_export_excludes_password_by_default(self, tmp_path):
        svc = _make_service(tmp_path)
        conn = ConnectionConfig(name="secret", type="mysql", host="localhost",
                                password="hunter2", database="db")
        svc.cfg.upsert_connection(conn)

        result = svc.export_connection({"connection_name": "secret"})
        assert "password" not in result["connection"]

    def test_export_includes_password_when_requested(self, tmp_path):
        svc = _make_service(tmp_path)
        conn = ConnectionConfig(name="secret", type="mysql", host="localhost",
                                password="hunter2", database="db")
        svc.cfg.upsert_connection(conn)

        result = svc.export_connection({"connection_name": "secret", "include_secrets": True})
        assert result["connection"]["password"] == "hunter2"


class TestImportConnection:
    def test_import_new_connection(self, tmp_path):
        svc = _make_service(tmp_path)
        export_data = {
            "dbaide_export": {"version": 1, "type": "connection"},
            "connection": {"name": "imported", "type": "sqlite", "path": "/tmp/imported.db"},
            "joins": [],
            "annotations": [],
        }

        result = svc.import_connection({"data": export_data})
        assert result["name"] == "imported"
        assert "imported" in svc.cfg.connections()
        assert svc.cfg.connections()["imported"].type == "sqlite"

    def test_import_with_joins(self, tmp_path):
        svc = _make_service(tmp_path)
        export_data = {
            "dbaide_export": {"version": 1, "type": "connection"},
            "connection": {"name": "withjoins", "type": "sqlite", "path": "/tmp/x.db"},
            "joins": [{
                "table": "orders", "column": "user_id",
                "ref_table": "users", "ref_column": "id",
                "source": "user", "database": "main",
            }],
            "annotations": [],
        }

        result = svc.import_connection({"data": export_data})
        joins = svc.join_catalog._load("withjoins")
        assert len(joins) == 1
        assert joins[0]["table"] == "orders"

    def test_import_with_annotations(self, tmp_path):
        svc = _make_service(tmp_path)
        export_data = {
            "dbaide_export": {"version": 1, "type": "connection"},
            "connection": {"name": "withnotes", "type": "sqlite", "path": "/tmp/x.db"},
            "joins": [],
            "annotations": [{
                "scope": "table", "database": "main",
                "table": "users", "column": "",
                "note": "Core user table", "source": "user",
            }],
        }

        result = svc.import_connection({"data": export_data})
        anns = svc.annotations._load("withnotes")
        assert len(anns) == 1
        assert anns[0]["note"] == "Core user table"

    def test_import_overwrites_existing_connection(self, tmp_path):
        svc = _make_service(tmp_path)
        # Pre-existing connection.
        conn = ConnectionConfig(name="mydb", type="sqlite", path="/tmp/old.db")
        svc.cfg.upsert_connection(conn)

        export_data = {
            "dbaide_export": {"version": 1, "type": "connection"},
            "connection": {"name": "mydb", "type": "sqlite", "path": "/tmp/new.db"},
            "joins": [],
            "annotations": [],
        }

        svc.import_connection({"data": export_data})
        assert svc.cfg.connections()["mydb"].path == "/tmp/new.db"

    def test_import_merges_joins_no_duplicates(self, tmp_path):
        svc = _make_service(tmp_path)
        conn = ConnectionConfig(name="merge", type="sqlite", path="/tmp/x.db")
        svc.cfg.upsert_connection(conn)

        # Pre-existing join.
        svc.join_catalog.add("merge", {
            "table": "orders", "column": "user_id",
            "ref_table": "users", "ref_column": "id",
        }, source="user", database="main", fingerprint="fp")

        export_data = {
            "dbaide_export": {"version": 1, "type": "connection"},
            "connection": {"name": "merge", "type": "sqlite", "path": "/tmp/x.db"},
            "joins": [
                # Same join — should NOT duplicate.
                {"table": "orders", "column": "user_id",
                 "ref_table": "users", "ref_column": "id"},
                # New join — should be added.
                {"table": "orders", "column": "product_id",
                 "ref_table": "products", "ref_column": "id"},
            ],
            "annotations": [],
        }

        svc.import_connection({"data": export_data})
        joins = svc.join_catalog._load("merge")
        assert len(joins) == 2  # 1 existing + 1 new, not 3

    def test_import_rejects_invalid_format(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Not a valid"):
            svc.import_connection({"data": {"some": "garbage"}})


class TestExportAll:
    def test_export_all_connections_and_models(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.cfg.upsert_connection(ConnectionConfig(name="db1", type="sqlite", path="/tmp/1.db"))
        svc.cfg.upsert_connection(ConnectionConfig(name="db2", type="sqlite", path="/tmp/2.db"))
        svc.cfg.upsert_model(ModelConfig(name="gpt4", provider="openai_compatible",
                                         base_url="https://api.openai.com", model="gpt-4"))

        result = svc.export_all({})
        assert result["dbaide_export"]["type"] == "full"
        assert len(result["connections"]) == 2
        assert len(result["models"]) == 1
        assert result["models"][0]["name"] == "gpt4"

    def test_export_all_excludes_secrets_by_default(self, tmp_path):
        svc = _make_service(tmp_path)
        svc.cfg.upsert_model(ModelConfig(name="m1", provider="openai_compatible",
                                         api_key="sk-secret", model="gpt-4"))

        result = svc.export_all({})
        assert "api_key" not in result["models"][0]


class TestRoundTrip:
    def test_export_then_import(self, tmp_path):
        """Full round-trip: export from one service, import into a fresh one."""
        svc1 = _make_service(tmp_path / "src")
        conn = ConnectionConfig(name="roundtrip", type="mysql", host="db.example.com",
                                database="myapp", user="admin", password_env="DB_PASS")
        svc1.cfg.upsert_connection(conn)
        svc1.join_catalog.add("roundtrip", {
            "table": "orders", "column": "customer_id",
            "ref_table": "customers", "ref_column": "id",
        }, source="user", database="myapp", fingerprint="fp")
        svc1.annotations.add("roundtrip", scope="database", database="myapp",
                             table="", note="Production database — read-only access")

        exported = svc1.export_connection({"connection_name": "roundtrip"})

        # Import into a fresh service.
        svc2 = _make_service(tmp_path / "dst")
        result = svc2.import_connection({"data": exported})

        assert result["name"] == "roundtrip"
        imported_conn = svc2.cfg.connections()["roundtrip"]
        assert imported_conn.host == "db.example.com"
        assert imported_conn.user == "admin"

        joins = svc2.join_catalog._load("roundtrip")
        assert len(joins) == 1
        assert joins[0]["ref_table"] == "customers"

        anns = svc2.annotations._load("roundtrip")
        assert len(anns) == 1
        assert "Production" in anns[0]["note"]

    def test_full_export_then_import(self, tmp_path):
        """Full config round-trip: export all, import into fresh instance."""
        svc1 = _make_service(tmp_path / "src")
        svc1.cfg.upsert_connection(ConnectionConfig(name="a", type="sqlite", path="/x.db"))
        svc1.cfg.upsert_connection(ConnectionConfig(name="b", type="sqlite", path="/y.db"))
        svc1.cfg.upsert_model(ModelConfig(name="m1", model="gpt-4"))

        exported = svc1.export_all({})

        svc2 = _make_service(tmp_path / "dst")
        result = svc2.import_connection({"data": exported})  # full import via import_connection

        assert result["connections"] == 2
        assert result["models"] == 1
        assert "a" in svc2.cfg.connections()
        assert "b" in svc2.cfg.connections()
