from __future__ import annotations

import sys

from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.ui import DBAideDesktop


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
    cfg = ConfigManager()
    set_language(cfg.ui_language())
    set_theme(cfg.ui_theme())
    # Debug trace toggle (Settings) — capture full LLM prompts/responses so a copied
    # trace shows every stage's context. The env var still works and wins if set.
    if cfg.debug_trace():
        set_tracing(True)
    app = DBAideDesktop(DesktopService(cfg))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
