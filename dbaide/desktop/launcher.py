from __future__ import annotations

import sys

from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.ui import DBAideDesktop


def main(argv: list[str] | None = None) -> int:
    _ = argv or sys.argv[1:]
    from dbaide.i18n import set_language
    from dbaide.desktop.theme import set_theme
    cfg = ConfigManager()
    set_language(cfg.ui_language())
    set_theme(cfg.ui_theme())
    app = DBAideDesktop(DesktopService(cfg))
    app.run()
    return 0
