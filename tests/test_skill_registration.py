"""Tests for MCP server registration across the supported tool formats."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import dbaide.skill as skill


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect skill's $HOME to an isolated temp dir."""
    monkeypatch.setattr(skill, "_HOME", tmp_path)
    return tmp_path


# ── Registry sanity ──────────────────────────────────────────────────────────

def test_supported_tools_are_known_formats():
    for tool in skill.SUPPORTED_TOOLS:
        _rel, fmt = skill._TOOLS[tool]
        assert fmt in skill._SETUP
        assert fmt in skill._UNINSTALL
        assert fmt in skill._MODE


def test_tool_registry_is_path_map():
    # External callers (GUI/CLI) rely on TOOL_REGISTRY[tool] being a path str.
    for tool in skill.SUPPORTED_TOOLS:
        assert isinstance(skill.TOOL_REGISTRY[tool], str)


def test_unsupported_tools_removed():
    # Tools that cannot be configured via a written file must not be offered.
    for tool in ("aider", "trae", "qoder", "cline", "augment", "mimocode"):
        assert tool not in skill.SUPPORTED_TOOLS


def test_claude_uses_claude_json_not_settings():
    # The canonical Claude Code user-scope MCP file is ~/.claude.json.
    assert skill.TOOL_REGISTRY["claude"] == ".claude.json"


# ── mcpServers (JSON) format ─────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["claude", "cursor", "windsurf", "roo", "gemini", "qwen"])
def test_mcpservers_roundtrip(home, tool):
    path = Path(skill.setup_tool(tool, mode="full"))
    data = json.loads(path.read_text())
    assert data["mcpServers"]["dbaide"]["command"]
    assert skill.is_installed(tool)
    assert skill.installed_mode(tool) == "full"
    assert skill.uninstall_tool(tool) is True
    assert skill.installed_mode(tool) is None


def test_mcpservers_preserves_existing_keys(home):
    p = home / ".claude.json"
    p.write_text(json.dumps({"projects": {"x": 1}, "oauthAccount": "keep"}))
    skill.setup_tool("claude", mode="ask")
    data = json.loads(p.read_text())
    assert data["projects"] == {"x": 1}
    assert data["oauthAccount"] == "keep"
    assert skill.installed_mode("claude") == "ask"


def test_mcpservers_mode_parsing(home):
    skill.setup_tool("cursor", mode="tools")
    assert skill.installed_mode("cursor") == "tools"


# ── opencode (JSON, "mcp" key, command list) format ─────────────────────────

def test_opencode_format(home):
    path = Path(skill.setup_tool("opencode", mode="tools"))
    data = json.loads(path.read_text())
    entry = data["mcp"]["dbaide"]
    assert entry["type"] == "local"
    assert isinstance(entry["command"], list)
    assert "tools" in entry["command"]  # mode folded into command list
    assert skill.installed_mode("opencode") == "tools"
    assert skill.uninstall_tool("opencode") is True
    assert skill.installed_mode("opencode") is None


# ── codex (TOML) format ──────────────────────────────────────────────────────

def test_codex_toml_roundtrip(home):
    path = Path(skill.setup_tool("codex", mode="full"))
    parsed = tomllib.loads(path.read_text())
    assert parsed["mcp_servers"]["dbaide"]["command"]
    assert skill.installed_mode("codex") == "full"


def test_codex_preserves_other_content(home):
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n')
    skill.setup_tool("codex", mode="ask")
    parsed = tomllib.loads(p.read_text())
    assert parsed["model"] == "gpt-5"
    assert "other" in parsed["mcp_servers"]
    assert "dbaide" in parsed["mcp_servers"]
    assert skill.installed_mode("codex") == "ask"


def test_codex_no_duplicate_block_on_resetup(home):
    p = Path(skill.setup_tool("codex", mode="full"))
    skill.setup_tool("codex", mode="ask")
    skill.setup_tool("codex", mode="tools")
    assert p.read_text().count("[mcp_servers.dbaide]") == 1


def test_codex_uninstall_preserves_other_table(home):
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text('[mcp_servers.other]\ncommand = "x"\n')
    skill.setup_tool("codex", mode="full")
    assert skill.uninstall_tool("codex") is True
    parsed = tomllib.loads(p.read_text())
    assert "other" in parsed.get("mcp_servers", {})
    assert "dbaide" not in parsed.get("mcp_servers", {})


# ── setup_all / uninstall_all ────────────────────────────────────────────────

def test_setup_all_and_uninstall_all(home):
    results = skill.setup_all(mode="full")
    assert set(results.keys()) == set(skill.SUPPORTED_TOOLS)
    for tool in skill.SUPPORTED_TOOLS:
        assert skill.is_installed(tool)
    removed = skill.uninstall_all()
    assert set(removed) == set(skill.SUPPORTED_TOOLS)
    for tool in skill.SUPPORTED_TOOLS:
        assert not skill.is_installed(tool)


def test_unknown_tool_raises(home):
    with pytest.raises(KeyError):
        skill.setup_tool("nonexistent")


def test_invalid_mode_raises(home):
    with pytest.raises(ValueError):
        skill.setup_tool("claude", mode="bogus")
