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


def test_colorref_packs_bgr():
    from PyQt6.QtGui import QColor

    from dbaide.desktop.window_chrome import _colorref

    c = QColor("#07080a")
    assert _colorref(c) == (0x0A << 16) | (0x08 << 8) | 0x07


def test_integrated_title_bar_disabled_off_macos(monkeypatch):
    monkeypatch.setattr("dbaide.desktop.window_chrome.sys.platform", "win32")
    assert supports_integrated_title_bar() is False
    monkeypatch.setattr("dbaide.desktop.window_chrome.sys.platform", "linux")
    assert supports_integrated_title_bar() is False


def test_uses_windows_custom_caption_only_on_win32(monkeypatch):
    from dbaide.desktop.windows_caption import uses_windows_custom_caption

    monkeypatch.setattr("dbaide.desktop.windows_caption.sys.platform", "win32")
    assert uses_windows_custom_caption() is True
    monkeypatch.setattr("dbaide.desktop.windows_caption.sys.platform", "darwin")
    assert uses_windows_custom_caption() is False
