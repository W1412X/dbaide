from __future__ import annotations

import sys

from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.ui import DBAideDesktop


def main(argv: list[str] | None = None) -> int:
    _ = argv or sys.argv[1:]
    app = DBAideDesktop(DesktopService(ConfigManager()))
    app.run()
    return 0
