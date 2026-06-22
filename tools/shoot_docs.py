"""Generate one documentation screenshot scenario per process.

Usage:
    PYTHONPATH=. ./venv/bin/python tools/shoot_docs.py <scenario>

This script is intentionally strict for chart answers: it uses the real
Qt WebEngine answer renderer and does not silently downgrade to fallback text or
composited chart assets. Run it from a GUI-capable desktop session.
"""

from __future__ import annotations

import os
import subprocess
import sys
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
    raise SystemExit("PyQt6-WebEngine is required for docs screenshots.")

from PyQt6.QtWidgets import QApplication

from dbaide.desktop.theme import app_style

import tools.shoot_promo as promo


def _verify_webengine_runtime() -> None:
    if os.environ.get("DBAIDE_WEBENGINE_RUNTIME_VERIFIED") == "1":
        return
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_webengine_runtime.py")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        os.environ["DBAIDE_WEBENGINE_RUNTIME_VERIFIED"] = "1"
        return
    detail = ""
    if os.environ.get("DBAIDE_DEBUG_WEBENGINE_PROBE") == "1":
        raw = (result.stderr or result.stdout or "").strip()
        if raw:
            detail = f"\n{raw}"
    raise SystemExit(
        "Qt WebEngine runtime probe failed. Run docs screenshot generation from a GUI-capable "
        f"desktop session.{detail}"
    )

def _run_window_scenario(name: str) -> list[str]:
    app = QApplication.instance() or QApplication([sys.argv[0] or "shoot_docs"])
    app.setStyleSheet(app_style())
    win, service = promo._build_window(app)
    if name == "assets":
        paths = [promo.show_assets_initializing(app, win)]
    elif name == "thinking":
        paths = [promo.show_runtime_thinking(app, win)]
    elif name == "trace":
        paths = [promo.show_trace_timeline(app, win)]
    elif name == "analysis":
        paths = [promo.show_chart_answer(app, win)[0]]
    elif name == "breakdown":
        paths = [promo.show_chart_answer(app, win)[1]]
    elif name == "clarify":
        paths = [promo.show_clarification(app, win)]
    elif name == "sql":
        paths = [promo.show_database_client(app, win)[0]]
    elif name == "table":
        paths = [promo.show_database_client(app, win)[1]]
    elif name == "field":
        paths = [promo.show_developer_field_exploration(app, win)]
    elif name == "dep-tree":
        paths = [promo.show_developer_dependency_tree(app, win)]
    elif name == "audit":
        paths = [promo.show_developer_consistency_audit(app, win)]
    elif name == "settings-connections":
        paths = [promo.show_settings_page(app, service, page="connections", name="10-settings-connections")]
    elif name == "settings-models":
        paths = [promo.show_settings_page(app, service, page="models", name="11-settings-models")]
    elif name == "settings-resources":
        paths = [promo.show_settings_page(app, service, page="resources", name="12-settings-resources")]
    elif name == "settings-integrations":
        paths = [promo.show_settings_page(app, service, page="integrations", name="13-settings-integrations")]
    elif name == "backup":
        paths = [promo.show_backup_and_setup(app, service)[0]]
    elif name == "build-dialog":
        paths = [promo.show_backup_and_setup(app, service)[1]]
    elif name == "connection-dialog":
        paths = [promo.show_backup_and_setup(app, service)[2]]
    else:
        raise SystemExit(f"unknown scenario: {name}")
    win.close()
    app.closeAllWindows()
    app.quit()
    return [str(path) for path in paths]


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if len(argv) != 1:
        print(
            "usage: python tools/shoot_docs.py "
            "<assets|thinking|trace|analysis|breakdown|clarify|sql|table|field|audit|"
            "settings-connections|settings-models|settings-resources|settings-integrations|"
            "backup|build-dialog|connection-dialog>"
        )
        return 2
    _verify_webengine_runtime()
    paths = _run_window_scenario(argv[0])
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
