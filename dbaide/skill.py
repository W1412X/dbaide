"""MCP server registration for AI coding tool integration.

Each supported tool stores its MCP server config in a JSON file under
the user's home directory.  ``setup_tool`` / ``uninstall_tool`` write or
remove the ``dbaide`` entry from that file; ``is_installed`` checks
whether the entry exists.

The MCP server itself lives in ``dbaide.mcp_server`` and is started via
``dbaide mcp``.

Three modes are available:

    full  — exposes both the AI pipeline (ask) and atomic DB tools
    ask   — only the high-level ask tool (AI does everything)
    tools — only atomic DB tools (external agent drives reasoning)

``setup_tool`` always removes the old entry first (delete-before-install)
to ensure a clean switch when changing modes.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

_HOME = Path.home()

VALID_MODES = ("full", "ask", "tools")
DEFAULT_MODE = "full"


def _dbaide_command() -> str:
    """Return the best command string to start the MCP server."""
    exe = shutil.which("dbaide")
    if exe:
        return exe
    return sys.executable


def _mcp_entry(mode: str = DEFAULT_MODE) -> dict[str, Any]:
    """The mcpServers entry for dbaide."""
    exe = _dbaide_command()
    if exe.endswith("dbaide"):
        args = ["mcp"]
    else:
        args = ["-m", "dbaide.mcp_server"]
    if mode and mode != DEFAULT_MODE:
        args.extend(["--mode", mode])
    return {"command": exe, "args": args}


# ── Per-tool config locations ───────────────────────────────────────────────

# Each tool has a JSON file where MCP servers are registered under the
# "mcpServers" key.  The path is relative to $HOME.
TOOL_REGISTRY: dict[str, str] = {
    "claude":    ".claude/settings.json",
    "cursor":    ".cursor/mcp.json",
    "windsurf":  ".codeium/windsurf/mcp_config.json",
    "cline":     ".cline/mcp_settings.json",
    "roo":       ".roo/mcp.json",
    "trae":      ".trae/mcp.json",
    "codex":     ".codex/mcp.json",
    "augment":   ".augment/mcp.json",
    "opencode":  ".opencode/mcp.json",
    "qoder":     ".qoder/mcp.json",
    "mimocode":  ".mimocode/mcp.json",
    "aider":     ".aider/mcp.json",
}

SUPPORTED_TOOLS = sorted(TOOL_REGISTRY.keys())

SERVER_KEY = "dbaide"


# ── Read / write helpers ────────────────────────────────────────────────────

def _config_path(tool: str) -> Path:
    return _HOME / TOOL_REGISTRY[tool]


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file first, then rename — avoids corrupting
    # the config if the process is killed mid-write.
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ── Public API ──────────────────────────────────────────────────────────────

def is_installed(tool: str) -> bool:
    """Return True if the dbaide MCP server is registered for *tool*."""
    if tool not in TOOL_REGISTRY:
        return False
    data = _read_config(_config_path(tool))
    return SERVER_KEY in (data.get("mcpServers") or {})


def installed_mode(tool: str) -> str | None:
    """Return the mode of the currently installed entry, or None if not installed.

    Parses the args list to find ``--mode <value>``.  Returns "full" when
    ``--mode`` is absent (the default).
    """
    if tool not in TOOL_REGISTRY:
        return None
    data = _read_config(_config_path(tool))
    entry = (data.get("mcpServers") or {}).get(SERVER_KEY)
    if entry is None:
        return None
    args = entry.get("args") or []
    for i, arg in enumerate(args):
        if arg == "--mode" and i + 1 < len(args):
            v = args[i + 1]
            return v if v in VALID_MODES else DEFAULT_MODE
    return DEFAULT_MODE


def setup_tool(tool: str, *, mode: str = DEFAULT_MODE) -> str:
    """Register the dbaide MCP server for *tool*.

    Always removes the old entry first (delete-before-install) to ensure
    a clean switch when changing modes.  Returns the config path.
    """
    if tool not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool: {tool}. Supported: {', '.join(SUPPORTED_TOOLS)}")
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}. Valid: {', '.join(VALID_MODES)}")

    path = _config_path(tool)
    data = _read_config(path)
    servers = data.setdefault("mcpServers", {})
    servers.pop(SERVER_KEY, None)
    servers[SERVER_KEY] = _mcp_entry(mode)
    _write_config(path, data)
    return str(path)


def uninstall_tool(tool: str) -> bool:
    """Remove the dbaide MCP server entry.  Returns True if it was present."""
    if tool not in TOOL_REGISTRY:
        return False
    path = _config_path(tool)
    data = _read_config(path)
    servers = data.get("mcpServers") or {}
    if SERVER_KEY not in servers:
        return False
    del servers[SERVER_KEY]
    _write_config(path, data)
    return True


def setup_all(*, mode: str = DEFAULT_MODE) -> dict[str, str]:
    """Register for ALL tools.  Returns {tool: config_path}."""
    return {tool: setup_tool(tool, mode=mode) for tool in SUPPORTED_TOOLS}


def uninstall_all() -> list[str]:
    """Unregister from ALL tools.  Returns list of tools that were removed."""
    return [tool for tool in SUPPORTED_TOOLS if uninstall_tool(tool)]
