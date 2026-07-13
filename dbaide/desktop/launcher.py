from __future__ import annotations

import sys


def _verify_webengine_import() -> int:
    """Exit 0 when Qt WebEngine imports cleanly (used by frozen-bundle CI smoke)."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication

        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        print("webengine ok")
        return 0
    except Exception as exc:
        print(f"webengine import failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if "--verify-webengine" in argv:
        return _verify_webengine_import()

    # A CLI subcommand (e.g. the MCP server, which a packaged install registers against THIS
    # same binary) must run headless — never spin up the desktop GUI. The GUI is launched with
    # no positional args (double-click) or only flags; any leading non-flag token is a CLI
    # subcommand, so delegate to the CLI BEFORE importing or initializing Qt (otherwise the app
    # window pops up when a coding tool starts the MCP server).
    # Also map the legacy `-m dbaide.mcp_server` registration (a frozen binary can't run `-m`,
    # so older configs would otherwise launch the GUI) to the `mcp` subcommand.
    if argv[:2] == ["-m", "dbaide.mcp_server"]:
        from dbaide.cli import main as cli_main
        return cli_main(["mcp", *argv[2:]])
    if argv and (not argv[0].startswith("-") or argv[0] in {"--version", "-h", "--help"}):
        from dbaide.cli import main as cli_main
        return cli_main(argv)

    # The desktop UI is a client of the same backend as every MCP integration.
    # Start/verify that backend before importing Qt or any service implementation;
    # there is intentionally no in-process DesktopService fallback.
    from dbaide.config import ConfigManager
    from dbaide.skill import SUPPORTED_TOOLS, installed_mode, migrate_installed_to_http
    from dbaide.mcp_http import (
        autostart_enabled,
        ensure_daemon,
        install_daemon_autostart,
        is_daemon_running,
    )
    cfg = ConfigManager()
    daemon_dir = cfg.path.parent / "mcp"
    if autostart_enabled():
        install_daemon_autostart(config_dir=daemon_dir)
        ensure_daemon(config_dir=daemon_dir)
    elif not is_daemon_running(config_dir=daemon_dir):
        raise RuntimeError(
            "DBAIDE_MCP_AUTOSTART=0 but the externally managed DBAide backend is not running"
        )

    if any(installed_mode(tool) is not None for tool in SUPPORTED_TOOLS):
        try:
            migrate_installed_to_http()
        except Exception:
            # Integration repair is independent of the GUI-to-daemon channel.
            import logging
            logging.getLogger("dbaide").exception("Could not migrate legacy MCP registrations")

    from dbaide.desktop.platform_ui import ensure_webengine_before_qapplication

    webengine_ok = ensure_webengine_before_qapplication()
    from dbaide.observability.app_logging import setup_app_logging
    setup_app_logging()
    if not webengine_ok:
        import logging
        logging.getLogger("dbaide").error(
            "Qt WebEngine failed to initialize before the QApplication — charts will not "
            "render (they appear as [title] placeholders) and answers fall back to plain "
            "text. Check that PyQt6-WebEngine is installed in the running interpreter."
        )
    from dbaide.i18n import set_language
    from dbaide.desktop.theme import set_theme
    from dbaide.agent.llm_trace import set_tracing
    set_language(cfg.ui_language())
    set_theme(cfg.ui_theme())
    # Debug trace toggle (Settings) — capture full LLM prompts/responses so a copied
    # trace shows every stage's context. The env var still works and wins if set.
    if cfg.debug_trace():
        set_tracing(True)
    from dbaide.desktop.remote_service import RemoteDesktopService
    from dbaide.desktop.ui import DBAideDesktop
    app = DBAideDesktop(RemoteDesktopService(config_dir=daemon_dir))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
