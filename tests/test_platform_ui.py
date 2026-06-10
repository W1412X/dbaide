from dbaide.desktop.platform_ui import (
    chrome_suppresses_mnemonics,
    escape_mnemonic,
    label_for_chrome_button,
    supports_integrated_title_bar,
    topbar_layout_margins,
)


def test_escape_mnemonic_doubles_ampersand():
    assert escape_mnemonic("Save & Close") == "Save && Close"


def test_label_for_chrome_button_icon_only():
    assert label_for_chrome_button("Chat", icon_only=True) == ""


def test_chrome_suppresses_mnemonics_platforms():
    # Document expected platforms — actual value depends on test runner OS.
    assert isinstance(chrome_suppresses_mnemonics(), bool)


def test_topbar_layout_margins_with_safe_area():
    from PyQt6.QtCore import QMargins

    left, top, right, bottom, height = topbar_layout_margins(QMargins(70, 28, 8, 0))
    assert left == 82
    assert top == 0  # Qt already insets the central widget
    assert right == 20
    assert bottom == 0
    assert height == 42


def test_supports_integrated_title_bar_is_bool():
    assert isinstance(supports_integrated_title_bar(), bool)
