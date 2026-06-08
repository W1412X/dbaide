from dbaide.config import CONFIG_VERSION, ConfigManager, migrate_config, sanitize_config_data
from dbaide.core.errors import ErrorCode
from dbaide.llm_errors import classify_llm_error, is_llm_related, user_message_for_error


def test_migrate_legacy_config_adds_meta_version():
    data, changed = migrate_config({"connections": {}, "models": {}})
    assert changed is True
    assert data["meta"]["config_version"] == CONFIG_VERSION


def test_config_manager_migrates_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[connections.local]\ntype = "sqlite"\npath = "/tmp/x.db"\n',
        encoding="utf-8",
    )
    cfg = ConfigManager(path=path)
    assert cfg.config_version() == CONFIG_VERSION
    reloaded = path.read_text(encoding="utf-8")
    assert "[meta]" in reloaded
    assert "config_version" in reloaded


def test_sanitize_config_redacts_secrets():
    data = {
        "meta": {"config_version": CONFIG_VERSION},
        "connections": {"local": {"type": "sqlite", "path": "/x.db", "password": "secret"}},
        "models": {"default": {"provider": "openai_compatible", "api_key": "sk-test"}},
    }
    clean = sanitize_config_data(data)
    assert clean["connections"]["local"]["password"] == "***"
    assert clean["models"]["default"]["api_key"] == "***"


def test_classify_llm_unconfigured():
    err = classify_llm_error(RuntimeError("No LLM model configured."))
    assert err.code == ErrorCode.MODEL_UNAVAILABLE
    assert "Settings" in user_message_for_error(err) or "设置" in user_message_for_error(err)


def test_classify_llm_auth():
    err = classify_llm_error(RuntimeError("HTTP 401 Unauthorized"))
    assert err.code == ErrorCode.MODEL_UNAVAILABLE


def test_classify_llm_rate_limit_retryable():
    err = classify_llm_error(RuntimeError("429 Too Many Requests"))
    assert err.code == ErrorCode.LLM_ERROR
    assert err.retryable is True


def test_is_llm_related_heuristic():
    assert is_llm_related(RuntimeError("OpenAI API error")) is True
    assert is_llm_related(ValueError("table not found")) is False
