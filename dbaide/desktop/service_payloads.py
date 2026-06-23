from __future__ import annotations

from typing import Any

from dbaide.models import ConnectionConfig, ModelConfig


def to_payload(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [to_payload(item) for item in obj]
    if isinstance(obj, tuple):
        return [to_payload(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): to_payload(value) for key, value in obj.items()}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__slots__"):
        return {slot: to_payload(getattr(obj, slot)) for slot in obj.__slots__ if hasattr(obj, slot)}
    if hasattr(obj, "__dict__"):
        return {key: to_payload(value) for key, value in obj.__dict__.items()}
    return str(obj)


def connection_payload(conn: ConnectionConfig, *, has_assets: bool) -> dict[str, Any]:
    target = conn.path if conn.type == "sqlite" else f"{conn.host}:{conn.port or ''}/{conn.database}"
    return {
        "name": conn.name,
        "type": conn.type,
        "database": conn.database,
        "host": conn.host,
        "port": conn.port,
        "user": conn.user,
        "password_env": conn.password_env,
        "has_password": bool(conn.password or conn.password_env),
        "path": conn.path,
        "target": target,
        "load_profile": getattr(conn, "load_profile", "production"),
        "session_timezone": getattr(conn, "session_timezone", "UTC"),
        "sslmode": getattr(conn, "sslmode", ""),
        "ssl_ca": getattr(conn, "ssl_ca", ""),
        "asset_status": "ready" if has_assets else "missing",
    }


def model_payload(model: ModelConfig) -> dict[str, Any]:
    return {
        "name": model.name,
        "provider": model.provider,
        "base_url": model.base_url,
        "api_key_env": model.api_key_env,
        "has_api_key": bool(model.api_key or model.api_key_env),
        "model": model.model,
        "timeout_seconds": model.timeout_seconds,
        "context_length": model.context_length,
    }


def validate_model_config(model: ModelConfig) -> None:
    if model.provider in {"none", ""}:
        return
    # anthropic / openai_responses default their base URL, so only the model ID and API key
    # are required there; openai_compatible needs an explicit base URL too.
    needs_base_url = model.provider == "openai_compatible"
    missing: list[str] = []
    if needs_base_url and not model.base_url.strip():
        missing.append("Base URL")
    if not model.model.strip():
        missing.append("Model ID")
    if not model.api_key.strip() and not model.api_key_env.strip():
        missing.append("API Key")
    if missing:
        suffix = (" All three are required for openai_compatible."
                  if needs_base_url else "")
        raise ValueError("Model configuration incomplete. Missing: " + ", ".join(missing) + "." + suffix)
