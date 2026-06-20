import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_mode_switch_buttons_fit_inside_container(qapp):
    from dbaide.desktop.components.icons import svg_icon
    from dbaide.desktop.views.topbar import ModeSwitch, _MODE_BTN_H, _MODE_CHROME_H, _MODE_PAD

    switch = ModeSwitch()
    switch.addTab(svg_icon("message-circle"), "Assistant")
    switch.addTab(svg_icon("terminal"), "Workbench")
    qapp.processEvents()

    inner = _MODE_CHROME_H - _MODE_PAD * 2
    assert switch.height() >= _MODE_CHROME_H
    for btn in switch._buttons:
        assert btn.height() <= inner
