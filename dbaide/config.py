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

# Bump when config.toml layout changes; ``ConfigManager`` migrates on load.
CONFIG_VERSION = 1

# Default number of agent runs (sessions) allowed to execute concurrently.
DEFAULT_MAX_CONCURRENT_RUNS = 6

# Valid keys for ConnectionConfig and ModelConfig
_CONNECTION_KEYS = {
    "name", "type", "database", "host", "port", "user", "password_env", "password", "path",
    "load_profile", "session_timezone", "sslmode", "ssl_ca", "table_allow", "table_deny",
}
_MODEL_KEYS = {"name", "provider", "base_url", "api_key_env", "api_key", "model", "timeout_seconds", "context_length"}


def _config_version(data: dict[str, Any]) -> int:
    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        return 0
    try:
        return max(0, int(meta.get("config_version") or 0))
    except (TypeError, ValueError):
        return 0


def migrate_config(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade legacy on-disk config to the current schema. Returns (data, changed)."""
    data = dict(data or {})
    version = _config_version(data)
    changed = False

    if version < 1:
        data.setdefault("connections", {})
        data.setdefault("models", {})
        data.setdefault("ui", {})
        data.setdefault("resource_defaults", {})
        meta = dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
        meta["config_version"] = CONFIG_VERSION
        data["meta"] = meta
        version = CONFIG_VERSION
        changed = True

    if version > CONFIG_VERSION:
        logger.warning(
            "config version %s is newer than this app (%s); reading best-effort",
            version,
            CONFIG_VERSION,
        )

    return data, changed


def sanitize_config_data(data: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets for debug bundles / support exports."""
    clean = migrate_config(dict(data or {}))[0]

    def _scrub(section: str, keys: frozenset[str]) -> None:
        groups = clean.get(section) or {}
        if not isinstance(groups, dict):
            return
        for name, item in groups.items():
            if not isinstance(item, dict):
                continue
            for key in keys:
                if key in item and item[key]:
                    item[key] = "***"

    _scrub("connections", frozenset({"password"}))
    _scrub("models", frozenset({"api_key"}))
    return clean


def _toml_key(name: str) -> str:
    """Quote a TOML key/table-path segment if it contains special characters.

    A bare key in TOML may only contain ``[A-Za-z0-9_-]``.  Connection and
    model names that contain dots, spaces, or other characters must be quoted
    so ``[connections.my.server]`` becomes ``[connections."my.server"]``.
    """
    if name and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in name):
        return name
    return _toml_quote(name)


def _toml_quote(value: str) -> str:
    # Escape per the TOML basic-string spec. Without escaping control characters
    # (newline/tab/etc.) a value such as a pasted password produces invalid TOML,
    # which on the next load is silently treated as an empty config — wiping every
    # saved connection and model.
    text = value.replace("\\", "\\\\").replace('"', '\\"')
    for raw, esc in (("\n", "\\n"), ("\t", "\\t"), ("\r", "\\r"), ("\b", "\\b"), ("\f", "\\f")):
        text = text.replace(raw, esc)
    # Any remaining control character (ord < 0x20) → \uXXXX so the TOML stays valid.
    text = "".join(ch if ord(ch) >= 0x20 else f"\\u{ord(ch):04X}" for ch in text)
    return '"' + text + '"'


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
        parse_failed = False
        if self.path.exists():
            try:
                with self.path.open("rb") as fh:
                    self._data = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                logger.warning("failed to parse config %s: %s", self.path, exc)
                backup = self.path.with_suffix(".toml.bak")
                try:
                    import shutil
                    shutil.copy2(self.path, backup)
                    logger.warning("corrupt config backed up to %s", backup)
                except OSError:
                    pass
                self._data = {"connections": {}, "models": {}}
                parse_failed = True
            except OSError as exc:
                logger.warning("failed to read config %s: %s", self.path, exc)
                self._data = {"connections": {}, "models": {}}
                parse_failed = True
        else:
            self._data = {"connections": {}, "models": {}}
        # Previous versions of _render_toml placed default_connection /
        # default_model after the [meta] header, so TOML scoping absorbed
        # them into the meta dict.  Pull them back to the root on reload.
        meta = self._data.get("meta")
        if isinstance(meta, dict):
            for stale in ("default_connection", "default_model"):
                if stale in meta:
                    self._data.setdefault(stale, meta.pop(stale))
        self._data, migrated = migrate_config(self._data)
        if migrated:
            logger.info("migrated config to version %s", CONFIG_VERSION)
            # Do NOT save when the config file failed to parse — that would
            # overwrite the user's (possibly recoverable) config with empty data.
            if not parse_failed:
                try:
                    self.save()
                except OSError as exc:
                    logger.warning("failed to persist migrated config: %s", exc)

    def config_version(self) -> int:
        return _config_version(self._data)

    def ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_parent()
        meta = dict(self._data.get("meta") or {}) if isinstance(self._data.get("meta"), dict) else {}
        meta["config_version"] = CONFIG_VERSION
        self._data["meta"] = meta
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
        self._clear_policy_cache(cfg.name)

    def delete_connection(self, name: str) -> None:
        connections = self._data.setdefault("connections", {})
        connections.pop(name, None)
        if self._data.get("default_connection") == name:
            self._data["default_connection"] = next(iter(connections), "")
        self.save()
        self._clear_policy_cache(name)

    def model(self, name: str | None = None) -> ModelConfig:
        models_map = self.models()
        if not name:
            name = str(self._data.get("default_model") or "") or (next(iter(models_map), "") if models_map else "default")
        if name in models_map:
            return models_map[name]
        if models_map:
            logger.warning("model %r not found in config (available: %s); returning unconfigured stub", name, ", ".join(models_map))
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

    def max_concurrent_runs(self) -> int:
        """How many agent runs (one per session) may execute at once. A global
        app-level cap, distinct from the per-run database knobs in ResourcePolicy.
        Stored in ``[resource_defaults].max_concurrent_runs``; defaults to 6."""
        raw = self.resource_defaults().get("max_concurrent_runs")
        try:
            return max(1, min(16, int(raw)))
        except (TypeError, ValueError):
            return DEFAULT_MAX_CONCURRENT_RUNS

    def set_resource_defaults(self, values: dict[str, Any]) -> None:
        """Persist ``[resource_defaults]`` (drops None/empty values)."""
        clean = {k: v for k, v in (values or {}).items() if v is not None and v != ""}
        self._data["resource_defaults"] = clean
        self.save()
        self._clear_policy_cache()

    # ── UI language ──────────────────────────────────────────────────────────

    def ui_language(self) -> str:
        from dbaide.i18n import normalize
        ui = self._data.get("ui") or {}
        return normalize(ui.get("language") if isinstance(ui, dict) else None)

    def set_ui_language(self, lang: str) -> None:
        from dbaide.i18n import normalize
        ui = self._data.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["language"] = normalize(lang)
        self.save()

    # ── UI theme ────────────────────────────────────────────────────────────

    def ui_theme(self) -> str:
        """Return the saved theme name (``"dark"`` or ``"light"``)."""
        ui = self._data.get("ui") or {}
        name = str(ui.get("theme") or "dark").strip().lower() if isinstance(ui, dict) else "dark"
        return name if name in ("dark", "light") else "dark"

    def set_ui_theme(self, name: str) -> None:
        name = str(name or "dark").strip().lower()
        if name not in ("dark", "light"):
            name = "dark"
        ui = self._data.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["theme"] = name
        self.save()

    # ── Debug: capture full LLM prompts/responses into the trace ──────────────

    def debug_trace(self) -> bool:
        ui = self._data.get("ui") or {}
        return bool(ui.get("debug_trace", True)) if isinstance(ui, dict) else True

    def set_debug_trace(self, on: bool) -> None:
        ui = self._data.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["debug_trace"] = bool(on)
        self.save()

    # ── UI: stream the assistant's answer (progressive reveal) ────────────────

    def stream_answers(self) -> bool:
        """Whether to reveal the assistant's answer progressively (default True)."""
        ui = self._data.get("ui") or {}
        val = ui.get("stream_answers") if isinstance(ui, dict) else None
        return True if val is None else bool(val)

    def set_stream_answers(self, enabled: bool) -> None:
        ui = self._data.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["stream_answers"] = bool(enabled)
        self.save()

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
        # Root-level bare keys MUST come before any [table] header — in TOML,
        # keys after a header belong to that table until the next header.
        if data.get("default_connection"):
            lines.append(f"default_connection = {_toml_quote(str(data['default_connection']))}")
        if data.get("default_model"):
            lines.append(f"default_model = {_toml_quote(str(data['default_model']))}")
        if lines:
            lines.append("")
        meta = data.get("meta") or {}
        if isinstance(meta, dict) and meta:
            lines.append("[meta]")
            for key, value in meta.items():
                if value in (None, "", {}, []):
                    continue
                lines.append(f"{key} = {self._format_value(value)}")
            lines.append("")
        for table in ("ui", "resource_defaults"):
            values = data.get(table) or {}
            if isinstance(values, dict) and values:
                lines.append(f"[{table}]")
                for key, value in values.items():
                    if value in (None, "", {}, []):
                        continue
                    lines.append(f"{key} = {self._format_value(value)}")
                lines.append("")
        for section in ("connections", "models"):
            groups = data.get(section) or {}
            for name, values in groups.items():
                lines.append(f"[{section}.{_toml_key(name)}]")
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

    @staticmethod
    def _clear_policy_cache(instance: str | None = None) -> None:
        from dbaide.db import policy as _policy
        _policy.clear_cache(instance)
