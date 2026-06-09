from dbaide.config import CONFIG_VERSION, ConfigManager, migrate_config, sanitize_config_data
from dbaide.core.errors import ErrorCode
from dbaide.llm_errors import classify_llm_error, format_user_error, is_llm_related, user_message_for_error


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


# ── format_user_error: unified user-facing error messages ─────────────


def test_format_user_error_connection():
    msg = format_user_error(ConnectionError("Connection refused"))
    assert msg != "Connection refused"  # not the raw message
    assert "connect" in msg.lower() or "连接" in msg


def test_format_user_error_permission():
    msg = format_user_error(PermissionError("Access denied"))
    assert "permission" in msg.lower() or "权限" in msg


def test_format_user_error_timeout():
    msg = format_user_error(TimeoutError("deadline exceeded"))
    assert "timeout" in msg.lower() or "timed out" in msg.lower() or "超时" in msg


def test_format_user_error_syntax():
    msg = format_user_error(Exception("syntax error at or near 'SELEC'"))
    assert "syntax" in msg.lower() or "语法" in msg


def test_format_user_error_no_such_table():
    msg = format_user_error(Exception("no such table: orders_v2"))
    assert "table" in msg.lower() or "表" in msg


def test_format_user_error_no_such_column():
    msg = format_user_error(Exception("Unknown column 'foo' in 'field list'"))
    assert "column" in msg.lower() or "字段" in msg


def test_format_user_error_llm_delegates():
    """LLM-related errors should go through classify_llm_error."""
    msg = format_user_error(RuntimeError("OpenAI API error: 429 rate limit"))
    assert "rate" in msg.lower() or "限流" in msg


def test_format_user_error_short_generic():
    """Short unknown errors are wrapped in the sql_execution template."""
    msg = format_user_error(RuntimeError("boom"))
    assert "boom" in msg  # the detail is preserved


def test_format_user_error_long_generic():
    """Very long unknown errors get a generic catch-all."""
    long_msg = "x" * 300
    msg = format_user_error(RuntimeError(long_msg))
    assert long_msg not in msg  # not the raw 300-char string
    assert len(msg) < 200
