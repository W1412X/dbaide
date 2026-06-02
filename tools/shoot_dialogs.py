"""Offscreen screenshots of the dialogs. Usage:
PYTHONPATH=. QT_QPA_PLATFORM=offscreen .venv/bin/python tools/shoot_dialogs.py [tag]"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dbaide.desktop.theme import APP_STYLE  # noqa: E402

TAG = sys.argv[1] if len(sys.argv) > 1 else "base"
OUT = Path("/tmp/shots")
OUT.mkdir(exist_ok=True)


def grab(w, name):
    app = QApplication.instance()
    for _ in range(4):
        app.processEvents()
    w.grab().save(str(OUT / f"{TAG}__dlg_{name}.png"))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(APP_STYLE)

    from dbaide.desktop.dialogs.settings import SettingsDialog
    from dbaide.desktop.dialogs.connection import ConnectionForm, ConnectionDialog
    from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog

    def settings(page):
        return SettingsDialog(
            connections=[{"name": "shop", "type": "sqlite", "target": "/data/shop.db", "asset_status": "ready"},
                         {"name": "analytics", "type": "postgres", "target": "db:5432/analytics", "asset_status": "missing"}],
            models=[{"name": "gpt-4o", "provider": "openai", "model": "gpt-4o", "has_api_key": True,
                     "base_url": "https://api.openai.com/v1"}],
            resource_defaults={"values": {"max_inflight_queries": 5, "max_row_limit": 1000},
                               "presets": {"production": {"max_inflight_queries": 2}}},
            initial_page=page,
        )
    for page in ("connections", "models", "resources"):
        s = settings(page)
        s.resize(820, 560)
        grab(s, f"settings_{page}")

    b = BuildAssetsDialog(
        connection_name="shop",
        databases=[{"name": "main", "has_assets": False}, {"name": "analytics", "has_assets": True}],
        load_profile="production", default_profile_mode="light", default_max_workers=2,
    )
    b.resize(560, 460)
    grab(b, "build")

    try:
        d = ConnectionDialog()
        d.resize(560, 520)
        grab(d, "connection")
    except Exception as e:  # noqa: BLE001
        print("connection dialog skip:", e)

    print(f"dialog shots → {OUT} (tag={TAG})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
