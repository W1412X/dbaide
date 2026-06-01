import logging
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .models import ConnectionConfig, ModelConfig

logger = logging.getLogger("dbaide.config")

DEFAULT_CONFIG_DIR = Path.home() / ".dbaide"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"

# Valid keys for ConnectionConfig and ModelConfig
_CONNECTION_KEYS = {"name", "type", "database", "host", "port", "user", "password_env", "password", "path", "load_profile"}
_MODEL_KEYS = {"name", "provider", "base_url", "api_key_env", "api_key", "model", "timeout_seconds"}


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert an object with __slots__ to a dictionary."""
    if hasattr(obj, '__slots__'):
        return {slot: getattr(obj, slot) for slot in obj.__slots__}
    elif hasattr(obj, '__dict__'):
        return obj.__dict__.copy()
    else:
        return {}


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.environ.get("DBAIDE_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            try:
                with self.path.open("rb") as fh:
                    self._data = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                logger.warning("failed to parse config %s: %s", self.path, exc)
                self._data = {"connections": {}, "models": {}}
            except OSError as exc:
                logger.warning("failed to read config %s: %s", self.path, exc)
                self._data = {"connections": {}, "models": {}}
        else:
            self._data = {"connections": {}, "models": {}}

    def ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_parent()
        content = self._render_toml(self._data)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, str(self.path))
            logger.debug("config saved to %s", self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def connections(self) -> dict[str, ConnectionConfig]:
        raw = self._data.get("connections") or {}
        out: dict[str, ConnectionConfig] = {}
        for name, item in raw.items():
            if not isinstance(item, dict):
                logger.warning("skipping invalid connection entry: %s", name)
                continue
            payload = {k: v for k, v in item.items() if k in _CONNECTION_KEYS}
            payload.setdefault("name", name)
            payload.setdefault("type", "")
            try:
                out[name] = ConnectionConfig(**payload)
            except (TypeError, ValueError) as exc:
                logger.warning("skipping connection %s: %s", name, exc)
                continue
        return out

    def get_connection(self, name: str | None = None) -> ConnectionConfig:
        conns = self.connections()
        if not conns:
            raise ValueError("No connections configured. Run `dbaide connect add ...` first.")
        if not name:
            name = str(self._data.get("default_connection") or "") or next(iter(conns))
        if name not in conns:
            available = ", ".join(sorted(conns.keys()))
            raise KeyError(f"Connection not found: {name}. Available: {available}")
        return conns[name]

    def upsert_connection(self, cfg: ConnectionConfig, *, make_default: bool = False) -> None:
        self._data.setdefault("connections", {})
        payload = _to_dict(cfg)
        payload.pop("name", None)
        self._data["connections"][cfg.name] = payload
        if make_default or not self._data.get("default_connection"):
            self._data["default_connection"] = cfg.name
        self.save()

    def delete_connection(self, name: str) -> None:
        connections = self._data.setdefault("connections", {})
        connections.pop(name, None)
        if self._data.get("default_connection") == name:
            self._data["default_connection"] = next(iter(connections), "")
        self.save()

    def model(self, name: str | None = None) -> ModelConfig:
        models_map = self.models()
        if not name:
            name = str(self._data.get("default_model") or "") or (next(iter(models_map), "") if models_map else "default")
        if name in models_map:
            return models_map[name]
        return ModelConfig(name=name)

    def models(self) -> dict[str, ModelConfig]:
        raw = self._data.get("models") or {}
        out: dict[str, ModelConfig] = {}
        for name, item in raw.items():
            if not isinstance(item, dict):
                continue
            payload = {k: v for k, v in item.items() if k in _MODEL_KEYS}
            payload.setdefault("name", name)
            try:
                out[name] = ModelConfig(**payload)
            except (TypeError, ValueError):
                continue
        return out

    def upsert_model(self, cfg: ModelConfig, *, make_default: bool = False) -> None:
        self._data.setdefault("models", {})
        payload = _to_dict(cfg)
        name = payload.pop("name", None) or "default"
        self._data["models"][name] = payload
        if make_default or not self._data.get("default_model"):
            self._data["default_model"] = name
        self.save()

    def delete_model(self, name: str) -> None:
        models = self._data.setdefault("models", {})
        models.pop(name, None)
        if self._data.get("default_model") == name:
            self._data["default_model"] = next(iter(models), "")
        self.save()

    def set_default_model(self, name: str) -> None:
        if name and name not in self.models():
            raise KeyError(f"Model not found: {name}")
        self._data["default_model"] = name
        self.save()

    # ── Resource defaults (user-configurable numeric limits) ─────────────────

    def resource_defaults(self) -> dict[str, Any]:
        """Return the ``[resource_defaults]`` overrides (may be empty)."""
        raw = self._data.get("resource_defaults") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def set_resource_defaults(self, values: dict[str, Any]) -> None:
        """Persist ``[resource_defaults]`` (drops None/empty values)."""
        clean = {k: v for k, v in (values or {}).items() if v is not None and v != ""}
        self._data["resource_defaults"] = clean
        self.save()
        from dbaide.db import policy as _policy
        _policy.clear_cache()

    def policy_for(self, connection: "ConnectionConfig"):
        """Resolve the effective ResourcePolicy for a connection.

        Combines the connection's ``load_profile`` preset with the user's
        ``[resource_defaults]`` overrides, cached per instance name.
        """
        from dbaide.db.policy import resolve_policy
        return resolve_policy(
            load_profile=getattr(connection, "load_profile", "production"),
            overrides=self.resource_defaults(),
            instance=connection.name,
        )

    def _render_toml(self, data: dict[str, Any]) -> str:
        lines: list[str] = []
        if data.get("default_connection"):
            lines.append(f"default_connection = {_toml_quote(str(data['default_connection']))}")
            lines.append("")
        if data.get("default_model"):
            lines.append(f"default_model = {_toml_quote(str(data['default_model']))}")
            lines.append("")
        resource_defaults = data.get("resource_defaults") or {}
        if isinstance(resource_defaults, dict) and resource_defaults:
            lines.append("[resource_defaults]")
            for key, value in resource_defaults.items():
                if value in (None, "", {}, []):
                    continue
                lines.append(f"{key} = {self._format_value(value)}")
            lines.append("")
        for section in ("connections", "models"):
            groups = data.get(section) or {}
            for name, values in groups.items():
                lines.append(f"[{section}.{name}]")
                for key, value in (values or {}).items():
                    if value in (None, "", {}, []):
                        continue
                    lines.append(f"{key} = {self._format_value(value)}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _format_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._format_value(v) for v in value) + "]"
        if isinstance(value, dict):
            inner = ", ".join(f"{k} = {self._format_value(v)}" for k, v in value.items())
            return "{ " + inner + " }"
        return _toml_quote(str(value))
