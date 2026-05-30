import pytest
from dbaide.models import ConnectionConfig, ModelConfig


class TestConnectionConfig:
    def test_valid_sqlite(self):
        cfg = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        assert cfg.type == "sqlite"
        assert cfg.path == "/tmp/test.db"

    def test_valid_mysql(self):
        cfg = ConnectionConfig(name="test", type="mysql", host="localhost", port=3306)
        assert cfg.type == "mysql"
        assert cfg.host == "localhost"

    def test_valid_postgres(self):
        cfg = ConnectionConfig(name="test", type="postgres", host="db.example.com")
        assert cfg.type == "postgres"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid connection type"):
            ConnectionConfig(name="test", type="mssql")

    def test_sqlite_requires_path(self):
        with pytest.raises(ValueError, match="require --path"):
            ConnectionConfig(name="test", type="sqlite")

    def test_mysql_defaults_host(self):
        cfg = ConnectionConfig(name="test", type="mysql")
        assert cfg.host == "localhost"

    def test_port_range_valid(self):
        cfg = ConnectionConfig(name="test", type="mysql", host="h", port=3306)
        assert cfg.port == 3306

    def test_port_range_invalid_low(self):
        with pytest.raises(ValueError, match="Port must be"):
            ConnectionConfig(name="test", type="mysql", host="h", port=0)

    def test_port_range_invalid_high(self):
        with pytest.raises(ValueError, match="Port must be"):
            ConnectionConfig(name="test", type="mysql", host="h", port=99999)

    def test_name_stripped(self):
        cfg = ConnectionConfig(name="  test  ", type="sqlite", path="/tmp/x.db")
        assert cfg.name == "test"

    def test_type_normalized(self):
        cfg = ConnectionConfig(name="test", type="  SQLite  ", path="/tmp/x.db")
        assert cfg.type == "sqlite"

    def test_empty_type_allowed(self):
        cfg = ConnectionConfig(name="test", type="")
        assert cfg.type == ""


class TestModelConfig:
    def test_defaults(self):
        cfg = ModelConfig()
        assert cfg.provider == "none"
        assert cfg.timeout_seconds == 60

    def test_timeout_clamped_low(self):
        cfg = ModelConfig(timeout_seconds=0)
        assert cfg.timeout_seconds == 1

    def test_timeout_clamped_high(self):
        cfg = ModelConfig(timeout_seconds=9999)
        assert cfg.timeout_seconds == 600

    def test_provider_normalized(self):
        cfg = ModelConfig(provider="  OpenAI  ")
        assert cfg.provider == "openai"
