from PyQt6.QtCore import QMargins

from dbaide.desktop.window_chrome import (
    supports_integrated_title_bar,
    topbar_layout_margins,
)


def test_topbar_layout_margins_with_safe_area():
    left, top, right, bottom, height = topbar_layout_margins(QMargins(70, 28, 8, 0))
    assert left == 82
    assert top == 0
    assert right == 20
    assert bottom == 0
    assert height == 42


def test_supports_integrated_title_bar_is_bool():
    assert isinstance(supports_integrated_title_bar(), bool)
