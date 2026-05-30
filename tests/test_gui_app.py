import json
import sqlite3


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


def test_desktop_service_covers_assets_settings_errors_and_bridge_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("DBAIDE_ASSETS", str(tmp_path / "assets"))
    db = tmp_path / "app.db"
    make_db(db)

    from dbaide.config import ConfigManager
    from dbaide.desktop.service import DesktopService
    from dbaide.models import ConnectionConfig

    cfg = ConfigManager()
    cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path=str(db)), make_default=True)
    service = DesktopService(cfg)

    payload = service.dispatch("build_assets", {"name": "local", "profile_mode": "auto", "top_k": 5, "sample_limit": 10})
    assert payload["stats"]["columns"] == 4
    assert len(service.dispatch("schema_tree", {"name": "local"})) == 1

    hits = service.dispatch("search_assets", {"name": "local", "query": "email"})
    assert any(hit["path"] == "local.main.users.email" for hit in hits)

    try:
        service.dispatch("read_asset", {"path": "bad.path.that.has.too.many.parts"})
    except ValueError as exc:
        assert "Asset path" in str(exc)
    else:
        raise AssertionError("invalid asset path should fail")

    try:
        service.dispatch("save_connection", {"name": "bad", "type": "sqlite", "path": "", "port": "bad"})
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("invalid connection should fail")

    bootstrap = service.dispatch("bootstrap", {})
    assert bootstrap["connections"][0]["name"] == "local"
    assert bootstrap["connections"][0]["path"] == str(db)

    service.dispatch("save_model", {
        "base_url": "https://example.test/v1",
        "api_key": "secret",
        "model": "demo-model",
    })
    assert ConfigManager().model().model == "demo-model"
    assert "secret" not in json.dumps(service.dispatch("bootstrap", {}), ensure_ascii=False)
