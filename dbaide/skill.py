"""MCP server registration for AI coding tool integration.

Each supported tool stores its MCP server config in a file under the user's
home directory.  ``setup_tool`` / ``uninstall_tool`` write or remove the
``dbaide`` entry; ``is_installed`` checks whether the entry exists.

The MCP server itself lives in ``dbaide.mcp_server`` and is started via
``dbaide mcp``.

Three modes are available:

    full  — exposes both the AI pipeline (ask) and atomic DB tools
    ask   — only the high-level ask tool (AI does everything)
    tools — only atomic DB tools (external agent drives reasoning)

``setup_tool`` always removes the old entry first (delete-before-install)
to ensure a clean switch when changing modes.

Tools differ in *where* and in *what format* they store MCP servers.  Only
tools whose on-disk config format is documented and file-writable are
supported here — registering a file a tool never reads would be worse than
not registering at all.  Three formats are handled:

    mcpServers  — JSON ``{"mcpServers": {name: {command, args}}}``
                  (Claude Code, Cursor, Windsurf, Roo, Gemini CLI, Qwen Code)
    opencode    — JSON ``{"mcp": {name: {type, command:[exe, *args]}}}``
    codex_toml  — TOML ``[mcp_servers.name]`` with command / args
"""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

_HOME = Path.home()

VALID_MODES = ("full", "ask", "tools")
DEFAULT_MODE = "full"

SERVER_KEY = "dbaide"


def _dbaide_command() -> str:
    """Return the best command string to start the MCP server."""
    exe = shutil.which("dbaide")
    if exe:
        return exe
    return sys.executable


def _command_and_args(mode: str = DEFAULT_MODE) -> tuple[str, list[str]]:
    """Resolve the (command, args) pair used to launch the MCP server."""
    exe = _dbaide_command()
    if exe.endswith("dbaide"):
        args = ["mcp"]
    else:
        args = ["-m", "dbaide.mcp_server"]
    if mode and mode != DEFAULT_MODE:
        args.extend(["--mode", mode])
    return exe, args


def _mode_from_args(args: list[Any]) -> str:
    """Extract ``--mode <value>`` from an args list; default to ``full``."""
    for i, arg in enumerate(args):
        if arg == "--mode" and i + 1 < len(args):
            v = args[i + 1]
            return v if v in VALID_MODES else DEFAULT_MODE
    return DEFAULT_MODE


# ── Per-tool config locations + formats ─────────────────────────────────────

# Format identifiers.
_FMT_MCPSERVERS = "mcpServers"
_FMT_OPENCODE = "opencode"
_FMT_CODEX_TOML = "codex_toml"

# Path (relative to $HOME) and storage format for each supported tool.  Only
# tools with a documented, file-writable MCP config are listed; UI-only tools
# (Trae, Qoder) and tools without MCP support (Aider) are intentionally absent.
_TOOLS: dict[str, tuple[str, str]] = {
    # Claude Code reads user-scope MCP servers from ~/.claude.json (top-level
    # "mcpServers"), NOT ~/.claude/settings.json (which ignores mcpServers).
    "claude":   (".claude.json", _FMT_MCPSERVERS),
    "cursor":   (".cursor/mcp.json", _FMT_MCPSERVERS),
    "windsurf": (".codeium/windsurf/mcp_config.json", _FMT_MCPSERVERS),
    "roo":      (".roo/mcp.json", _FMT_MCPSERVERS),
    "gemini":   (".gemini/settings.json", _FMT_MCPSERVERS),
    "qwen":     (".qwen/settings.json", _FMT_MCPSERVERS),
    "opencode": (".config/opencode/opencode.json", _FMT_OPENCODE),
    "codex":    (".codex/config.toml", _FMT_CODEX_TOML),
}

# Back-compat alias: external callers (GUI settings dialog, CLI) read
# TOOL_REGISTRY[tool] to display the config path.
TOOL_REGISTRY: dict[str, str] = {tool: rel for tool, (rel, _fmt) in _TOOLS.items()}

SUPPORTED_TOOLS = sorted(_TOOLS.keys())


# ── Generic file helpers ────────────────────────────────────────────────────

def _config_path(tool: str) -> Path:
    return _HOME / TOOL_REGISTRY[tool]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file first, then rename — avoids corrupting the config
    # if the process is killed mid-write.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── Format: mcpServers (JSON) ───────────────────────────────────────────────

def _mcpservers_entry(mode: str) -> dict[str, Any]:
    exe, args = _command_and_args(mode)
    return {"command": exe, "args": args}


def _mcpservers_setup(path: Path, mode: str) -> None:
    data = _read_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    servers.pop(SERVER_KEY, None)
    servers[SERVER_KEY] = _mcpservers_entry(mode)
    _write_json(path, data)


def _mcpservers_uninstall(path: Path) -> bool:
    data = _read_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        return False
    del servers[SERVER_KEY]
    _write_json(path, data)
    return True


def _mcpservers_mode(path: Path) -> str | None:
    entry = (_read_json(path).get("mcpServers") or {}).get(SERVER_KEY)
    if not isinstance(entry, dict):
        return None
    return _mode_from_args(entry.get("args") or [])


# ── Format: opencode (JSON, key "mcp", command is [exe, *args]) ──────────────

def _opencode_entry(mode: str) -> dict[str, Any]:
    exe, args = _command_and_args(mode)
    return {"type": "local", "command": [exe, *args], "enabled": True}


def _opencode_setup(path: Path, mode: str) -> None:
    data = _read_json(path)
    servers = data.setdefault("mcp", {})
    if not isinstance(servers, dict):
        servers = data["mcp"] = {}
    servers.pop(SERVER_KEY, None)
    servers[SERVER_KEY] = _opencode_entry(mode)
    _write_json(path, data)


def _opencode_uninstall(path: Path) -> bool:
    data = _read_json(path)
    servers = data.get("mcp")
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        return False
    del servers[SERVER_KEY]
    _write_json(path, data)
    return True


def _opencode_mode(path: Path) -> str | None:
    entry = (_read_json(path).get("mcp") or {}).get(SERVER_KEY)
    if not isinstance(entry, dict):
        return None
    # opencode folds exe + args into a single "command" list.
    return _mode_from_args(entry.get("command") or [])


# ── Format: codex (TOML) ────────────────────────────────────────────────────

_CODEX_HEADER = f"[mcp_servers.{SERVER_KEY}]"


def _toml_str(value: str) -> str:
    """Serialize a Python string as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _strip_codex_block(text: str) -> str:
    """Remove the existing ``[mcp_servers.dbaide]`` table from TOML text,
    preserving everything else (comments, other tables, formatting)."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if skipping:
            # A new table header ends the block we're dropping.
            if stripped.startswith("["):
                skipping = False
            else:
                continue
        if stripped == _CODEX_HEADER or stripped == f'[mcp_servers."{SERVER_KEY}"]':
            skipping = True
            continue
        out.append(line)
    return "\n".join(out)


def _codex_setup(path: Path, mode: str) -> None:
    exe, args = _command_and_args(mode)
    args_toml = ", ".join(_toml_str(a) for a in args)
    block = (
        f"{_CODEX_HEADER}\n"
        f"command = {_toml_str(exe)}\n"
        f"args = [{args_toml}]\n"
    )
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    body = _strip_codex_block(existing).rstrip("\n")
    text = (body + "\n\n" + block) if body else block
    _atomic_write(path, text)


def _codex_uninstall(path: Path) -> bool:
    if not path.exists():
        return False
    if SERVER_KEY not in (_read_toml(path).get("mcp_servers") or {}):
        return False
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return False
    body = _strip_codex_block(existing).rstrip("\n")
    _atomic_write(path, (body + "\n") if body else "")
    return True


def _codex_mode(path: Path) -> str | None:
    entry = (_read_toml(path).get("mcp_servers") or {}).get(SERVER_KEY)
    if not isinstance(entry, dict):
        return None
    return _mode_from_args(entry.get("args") or [])


# ── Format dispatch ─────────────────────────────────────────────────────────

_SETUP = {
    _FMT_MCPSERVERS: _mcpservers_setup,
    _FMT_OPENCODE: _opencode_setup,
    _FMT_CODEX_TOML: _codex_setup,
}
_UNINSTALL = {
    _FMT_MCPSERVERS: _mcpservers_uninstall,
    _FMT_OPENCODE: _opencode_uninstall,
    _FMT_CODEX_TOML: _codex_uninstall,
}
_MODE = {
    _FMT_MCPSERVERS: _mcpservers_mode,
    _FMT_OPENCODE: _opencode_mode,
    _FMT_CODEX_TOML: _codex_mode,
}


# ── Public API ──────────────────────────────────────────────────────────────

def is_installed(tool: str) -> bool:
    """Return True if the dbaide MCP server is registered for *tool*."""
    return installed_mode(tool) is not None


def installed_mode(tool: str) -> str | None:
    """Return the mode of the currently installed entry, or None if absent.

    Parses the launch args to find ``--mode <value>``; returns "full" when
    ``--mode`` is absent (the default).
    """
    if tool not in _TOOLS:
        return None
    _rel, fmt = _TOOLS[tool]
    return _MODE[fmt](_config_path(tool))


def setup_tool(tool: str, *, mode: str = DEFAULT_MODE) -> str:
    """Register the dbaide MCP server for *tool*.

    Always removes the old entry first (delete-before-install) so changing
    modes yields a clean entry.  Returns the config path.
    """
    if tool not in _TOOLS:
        raise KeyError(f"Unknown tool: {tool}. Supported: {', '.join(SUPPORTED_TOOLS)}")
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}. Valid: {', '.join(VALID_MODES)}")
    _rel, fmt = _TOOLS[tool]
    path = _config_path(tool)
    _SETUP[fmt](path, mode)
    return str(path)


def uninstall_tool(tool: str) -> bool:
    """Remove the dbaide MCP server entry.  Returns True if it was present."""
    if tool not in _TOOLS:
        return False
    _rel, fmt = _TOOLS[tool]
    return _UNINSTALL[fmt](_config_path(tool))


def setup_all(*, mode: str = DEFAULT_MODE) -> dict[str, str]:
    """Register for ALL tools.  Returns {tool: config_path}."""
    return {tool: setup_tool(tool, mode=mode) for tool in SUPPORTED_TOOLS}


def uninstall_all() -> list[str]:
    """Unregister from ALL tools.  Returns list of tools that were removed."""
    return [tool for tool in SUPPORTED_TOOLS if uninstall_tool(tool)]
