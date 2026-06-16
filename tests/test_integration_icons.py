"""Integration tab brand icons ship with the desktop package."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dbaide.skill import SUPPORTED_TOOLS


def test_every_supported_tool_has_brand_icon():
    icons_dir = Path(__file__).resolve().parents[1] / "dbaide" / "desktop" / "assets" / "tool_icons"
    missing = [tool for tool in SUPPORTED_TOOLS if not (icons_dir / f"{tool}.png").is_file()]
    assert not missing, f"missing tool_icons: {', '.join(missing)}"


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_integrations_page_has_help_button(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog

    dlg = SettingsDialog(connections=[], models=[], initial_page="integrations")
    from PyQt6.QtWidgets import QToolButton

    buttons = dlg.findChildren(QToolButton)
    help = [b for b in buttons if b.objectName() == "integrationsHelpBtn"]
    assert len(help) == 1
    from dbaide.i18n import t

    assert help[0].toolTip() == t("settings.integrations.help_tooltip")
    dlg.deleteLater()
    qapp.processEvents()


def test_load_tool_icon_returns_non_empty_pixmap(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog

    for tool in SUPPORTED_TOOLS:
        px = SettingsDialog._load_tool_icon(tool)
        assert not px.isNull(), tool
        assert px.width() > 0 and px.height() > 0, tool
