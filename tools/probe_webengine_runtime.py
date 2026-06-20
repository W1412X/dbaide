"""Fail-fast probe for Qt WebEngine runtime availability.

This intentionally exercises a trivial ``QWebEngineView.setHtml`` flow so callers
can detect GUI-session / platform issues in a child process before attempting a
larger screenshot run.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-dev-shm-usage --disable-gpu",
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dbaide.desktop.platform_ui import ensure_webengine_before_qapplication

if not ensure_webengine_before_qapplication():
    raise SystemExit("PyQt6-WebEngine import failed.")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView


def main() -> int:
    app = QApplication.instance() or QApplication([sys.argv[0] or "probe_webengine_runtime"])
    view = QWebEngineView()
    view.resize(320, 120)
    view.setHtml("<html><body><div id='root'>ok</div></body></html>")
    view.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)
    view.close()
    app.closeAllWindows()
    app.quit()
    print("webengine runtime ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
