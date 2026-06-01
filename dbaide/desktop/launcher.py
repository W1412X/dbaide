from __future__ import annotations

import sys

from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.ui import DBAideDesktop


def main(argv: list[str] | None = None) -> int:
    _ = argv or sys.argv[1:]
    from dbaide.i18n import set_language
    cfg = ConfigManager()
    set_language(cfg.ui_language())
    app = DBAideDesktop(DesktopService(cfg))
    app.run()
    return 0
