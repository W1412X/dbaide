import json
import sqlite3

from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.models import ConnectionConfig
from tests.llm_mock import AgentMockLLM


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            status TEXT,
            created_at TEXT
        );
        INSERT INTO users VALUES
            (1, 'a@example.com', 'active', '2026-01-01'),
            (2, 'b@example.com', 'disabled', '2026-01-02');
        """
    )
    conn.commit()
    conn.close()


def test_gui_build_assets_uses_configured_store_and_serializes_slots_dataclass(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))

    cfg.upsert_connection(conn, make_default=True)
    payload = service.build_assets({"name": "local", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})
    stats = payload["stats"]

    assert stats["databases"] == 1
    assert stats["tables"] == 1
    assert stats["columns"] == 4
    assert (store.instance_dir("local") / "instance.json").exists()


def test_gui_find_reads_the_same_store_used_by_asset_builder(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))

    cfg.upsert_connection(conn, make_default=True)
    service.build_assets({"name": "local", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})
    result = service.search_assets({"name": "local", "query": "email"})

    assert any(hit["path"] == "local.main.users.email" for hit in result)


def test_gui_ask_uses_the_same_asset_store_for_lookup_questions(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))

    cfg.upsert_connection(conn, make_default=True)
    service.build_assets({"name": "local", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})
    service._safe_llm = lambda: AgentMockLLM()  # noqa: SLF001
    answer = service.ask({"connection_name": "local", "question": "email 在哪里", "execution_policy": "sql_only"})

    assert "local.main.users.email" in json.dumps(answer, ensure_ascii=False)


def test_gui_build_assets_handles_empty_database(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="empty", type="sqlite", path=str(db))

    cfg.upsert_connection(conn, make_default=True)
    payload = service.build_assets({"name": "empty", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})
    stats = payload["stats"]

    assert stats["databases"] == 1
    assert stats["tables"] == 0
    assert stats["columns"] == 0
    assert stats["errors"] == []


def test_gui_build_assets_accepts_database_subset(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    cfg.upsert_connection(conn, make_default=True)
    payload = service.build_assets({
        "name": "local",
        "databases": ["main"],
        "profile_mode": "auto",
        "top_k": 10,
        "sample_limit": 20,
    })
    assert payload["stats"]["databases"] == 1


def test_save_model_preserves_api_key_when_field_left_blank(tmp_path):
    cfg = ConfigManager(tmp_path / "config.toml")
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    service.save_model({
        "name": "default",
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1",
        "api_key": "secret-key",
        "model": "gpt-4",
    })
    service.save_model({
        "name": "default",
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1",
        "model": "gpt-4",
    })
    saved = cfg.model("default")
    assert saved.api_key == "secret-key"
    assert saved.base_url == "https://example.test/v1"


def test_save_model_reports_missing_fields(tmp_path):
    cfg = ConfigManager(tmp_path / "config.toml")
    service = DesktopService(cfg, AssetStore(tmp_path / "assets"))
    try:
        service.save_model({
            "name": "default",
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model": "",
        })
        raise AssertionError("expected validation error")
    except ValueError as exc:
        assert "Model ID" in str(exc)
