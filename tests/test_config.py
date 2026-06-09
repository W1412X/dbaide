import pytest

from dbaide.config import ConfigManager, _toml_key, _toml_quote
from dbaide.models import ConnectionConfig, ModelConfig


class TestTomlQuote:
    def test_simple_string(self):
        assert _toml_quote("hello") == '"hello"'

    def test_string_with_quotes(self):
        assert _toml_quote('say "hi"') == '"say \\"hi\\""'

    def test_string_with_backslash(self):
        assert _toml_quote("path\\to") == '"path\\\\to"'

    def test_empty_string(self):
        assert _toml_quote("") == '""'


class TestConfigManager:
    def test_init_creates_empty_config(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        assert cfg.connections() == {}

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        cfg.upsert_connection(conn)
        cfg2 = ConfigManager(path=path)
        assert "test" in cfg2.connections()
        assert cfg2.connections()["test"].type == "sqlite"

    def test_default_connection(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        cfg.upsert_connection(conn, make_default=True)
        assert cfg._data["default_connection"] == "test"

    def test_delete_connection(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        cfg.upsert_connection(conn)
        cfg.delete_connection("test")
        assert cfg.connections() == {}

    def test_get_connection_not_found(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        with pytest.raises(ValueError, match="No connections configured"):
            cfg.get_connection("nonexistent")

    def test_get_connection_no_default(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        cfg.upsert_connection(conn)
        result = cfg.get_connection(None)
        assert result.name == "test"

    def test_model_save_and_load(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        model = ModelConfig(name="default", provider="openai_compatible", base_url="http://test", model="gpt-4")
        cfg.upsert_model(model)
        loaded = cfg.model()
        assert loaded.provider == "openai_compatible"
        assert loaded.model == "gpt-4"

    def test_model_default(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        model = cfg.model()
        assert model.name == "default"

    def test_multiple_connections(self, tmp_path):
        cfg = ConfigManager(path=tmp_path / "config.toml")
        cfg.upsert_connection(ConnectionConfig(name="a", type="sqlite", path="/a.db"))
        cfg.upsert_connection(ConnectionConfig(name="b", type="sqlite", path="/b.db"))
        assert len(cfg.connections()) == 2

    def test_reload(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        cfg.upsert_connection(conn)
        cfg._data = {}
        cfg.reload()
        assert "test" in cfg.connections()

    def test_unicode_connection_name_roundtrip(self, tmp_path):
        """CJK and other non-ASCII names must be quoted in TOML keys (bare keys
        only allow [A-Za-z0-9_-] per the TOML 1.0 spec, but Python's str.isalnum()
        returns True for CJK).  Without quoting, the next reload would fail with a
        parse error, and the user's entire config becomes unreadable."""
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.upsert_connection(ConnectionConfig(name="数据库", type="sqlite", path="/tmp/cn.db"))
        cfg.upsert_connection(ConnectionConfig(name="café", type="sqlite", path="/tmp/fr.db"))
        cfg2 = ConfigManager(path=path)
        assert "数据库" in cfg2.connections()
        assert "café" in cfg2.connections()
        assert cfg2.connections()["数据库"].path == "/tmp/cn.db"

    def test_boolean_false_not_dropped(self, tmp_path):
        """Values like False and 0 must not be skipped by the render-time filter
        (``value in (None, '', {}, [])`` must NOT match False or 0)."""
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.set_stream_answers(False)
        cfg.set_debug_trace(False)
        cfg2 = ConfigManager(path=path)
        assert cfg2.stream_answers() is False
        assert cfg2.debug_trace() is False


class TestTomlKey:
    def test_ascii_alnum(self):
        assert _toml_key("myconn") == "myconn"

    def test_with_hyphen_underscore(self):
        assert _toml_key("my-conn_1") == "my-conn_1"

    def test_dot_quoted(self):
        assert _toml_key("my.server") == '"my.server"'

    def test_space_quoted(self):
        assert _toml_key("my server") == '"my server"'

    def test_cjk_quoted(self):
        assert _toml_key("数据库") == '"数据库"'

    def test_accented_quoted(self):
        assert _toml_key("café") == '"café"'

    def test_empty_quoted(self):
        assert _toml_key("") == '""'
