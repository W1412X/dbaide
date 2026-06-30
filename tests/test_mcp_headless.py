"""The MCP server must run headless — invoking it must never launch the desktop GUI.

Two layers: the GUI launcher routes a CLI subcommand (e.g. `mcp`) to the headless CLI before
touching Qt, and the MCP registration (skill.py) registers a command the app binary can run."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_launcher_routes_mcp_subcommand_to_cli_without_gui(monkeypatch):
    import dbaide.cli as cli
    captured = {}

    def fake_cli_main(argv=None):
        captured["argv"] = argv
        return 7

    monkeypatch.setattr(cli, "main", fake_cli_main)
    from dbaide.desktop.launcher import main as launcher_main
    # if routing failed, this would fall through to WebEngine + QApplication + the GUI
    rc = launcher_main(["mcp", "--mode", "tools"])
    assert rc == 7
    assert captured["argv"] == ["mcp", "--mode", "tools"]
    # legacy `-m dbaide.mcp_server` registrations are mapped to the mcp subcommand too
    assert launcher_main(["-m", "dbaide.mcp_server", "--mode", "ask"]) == 7
    assert captured["argv"] == ["mcp", "--mode", "ask"]


def test_registration_uses_mcp_subcommand_when_frozen(monkeypatch):
    import dbaide.skill as skill
    monkeypatch.setattr(skill.sys, "frozen", True, raising=False)
    monkeypatch.setattr(skill.sys, "executable", "/Apps/DBAide.app/Contents/MacOS/DBAide", raising=False)
    monkeypatch.setattr(skill.shutil, "which", lambda name: "/somewhere/dbaide")  # ignored when frozen
    exe, args = skill._command_and_args("tools")
    assert exe == "/Apps/DBAide.app/Contents/MacOS/DBAide"     # the app binary (self-routes mcp)
    assert args == ["mcp", "--mode", "tools"]                  # a subcommand, NOT `-m` (frozen can't)


def test_registration_uses_dash_m_for_plain_python(monkeypatch):
    import dbaide.skill as skill
    monkeypatch.setattr(skill.sys, "frozen", False, raising=False)
    monkeypatch.setattr(skill.shutil, "which", lambda name: None)
    monkeypatch.setattr(skill.sys, "executable", "/usr/bin/python3", raising=False)
    exe, args = skill._command_and_args("full")
    assert exe == "/usr/bin/python3" and args == ["-m", "dbaide.mcp_server"]
