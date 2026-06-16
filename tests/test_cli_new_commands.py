"""Tests for the new CLI subcommands: model, config, session, join, history, export, import, skill, setup."""

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(tmp_path, *args, expect_fail=False):
    env = {
        **os.environ,
        "DBAIDE_CONFIG": str(tmp_path / "config.toml"),
        "DBAIDE_ASSETS": str(tmp_path / "assets"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "dbaide.cli", *args],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if not expect_fail:
        assert result.returncode == 0, result.stderr
    return result


# ── model ────────────────────────────────────────────────────────────────────

def test_model_list_empty(tmp_path):
    r = run_cli(tmp_path, "model", "list")
    assert "No models configured" in r.stdout


def test_model_add_list_delete(tmp_path):
    run_cli(tmp_path, "model", "add", "mymodel", "--provider", "openai_compatible",
            "--base-url", "http://localhost:8080", "--model", "gpt-4", "--default")
    r = run_cli(tmp_path, "model", "list")
    assert "mymodel" in r.stdout
    assert "gpt-4" in r.stdout
    assert "*" in r.stdout

    run_cli(tmp_path, "model", "delete", "mymodel")
    r = run_cli(tmp_path, "model", "list")
    assert "No models configured" in r.stdout


def test_model_set_default(tmp_path):
    run_cli(tmp_path, "model", "add", "a", "--model", "m1")
    run_cli(tmp_path, "model", "add", "b", "--model", "m2")
    run_cli(tmp_path, "model", "set-default", "b")
    r = run_cli(tmp_path, "model", "list")
    for line in r.stdout.splitlines():
        if "b " in line or line.strip().startswith("b"):
            assert "*" in line
            break


# ── config ───────────────────────────────────────────────────────────────────

def test_config_show(tmp_path):
    r = run_cli(tmp_path, "config", "show")
    assert "Built-in presets" in r.stdout
    assert "production" in r.stdout


def test_config_set_and_reset(tmp_path):
    run_cli(tmp_path, "config", "set", "max_workers", "8")
    r = run_cli(tmp_path, "config", "show")
    assert "max_workers = 8" in r.stdout

    run_cli(tmp_path, "config", "reset")
    r = run_cli(tmp_path, "config", "show")
    assert "none — using built-in defaults" in r.stdout


# ── skill ────────────────────────────────────────────────────────────────────

def test_skill_output(tmp_path):
    r = run_cli(tmp_path, "skill")
    assert "DBAide" in r.stdout
    assert "dbaide ask" in r.stdout
    assert "Command Catalog" in r.stdout


def test_skill_export_to_file(tmp_path):
    out = tmp_path / "skill.md"
    run_cli(tmp_path, "skill", "--out", str(out))
    assert out.exists()
    assert "DBAide" in out.read_text()


# ── setup ────────────────────────────────────────────────────────────────────

def test_setup_claude_global(tmp_path):
    """setup claude writes to ~/.claude/commands/ (global config)."""
    r = run_cli(tmp_path, "setup", "claude")
    from pathlib import Path
    target = Path.home() / ".claude" / "commands" / "dbaide.md"
    assert target.exists()
    assert "DBAide" in target.read_text()


def test_setup_claude_with_project(tmp_path):
    """setup claude --project also writes project-level file."""
    project = tmp_path / "myproject"
    project.mkdir()
    run_cli(tmp_path, "setup", "claude", "--project", str(project))
    target = project / ".claude" / "commands" / "dbaide.md"
    assert target.exists()
    assert "DBAide" in target.read_text()


def test_setup_cursor_global(tmp_path):
    r = run_cli(tmp_path, "setup", "cursor")
    from pathlib import Path
    target = Path.home() / ".cursor" / "rules" / "dbaide.mdc"
    assert target.exists()
    content = target.read_text()
    assert "alwaysApply: true" in content
    assert "DBAide" in content


def test_setup_unknown_tool(tmp_path):
    r = run_cli(tmp_path, "setup", "nonexistent", expect_fail=True)
    assert r.returncode == 1
    assert "unknown tool" in r.stderr


def test_setup_all(tmp_path):
    """--all injects into every supported tool's global config."""
    r = run_cli(tmp_path, "setup", "--all")
    from dbaide.skill import SUPPORTED_TOOLS
    for tool in SUPPORTED_TOOLS:
        assert tool in r.stdout


# ── export / import ──────────────────────────────────────────────────────────

def test_export_all_empty(tmp_path):
    r = run_cli(tmp_path, "export", "--all")
    data = json.loads(r.stdout)
    assert data["dbaide_export"]["type"] == "full"
    assert data["connections"] == []


def test_export_import_round_trip(tmp_path):
    run_cli(tmp_path, "model", "add", "test-model", "--model", "gpt-4")
    out_file = tmp_path / "export.json"
    run_cli(tmp_path, "export", "--all", "--out", str(out_file))
    assert out_file.exists()

    # Import into a fresh config
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    env = {
        **os.environ,
        "DBAIDE_CONFIG": str(fresh / "config.toml"),
        "DBAIDE_ASSETS": str(fresh / "assets"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "dbaide.cli", "import", str(out_file)],
        cwd=Path(__file__).parents[1],
        env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "import complete" in result.stdout
