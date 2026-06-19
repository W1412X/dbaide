from dbaide.desktop.theme import Theme, app_style, set_theme


def _rule(style: str, selector: str, *, until: str) -> str:
    start = style.index(selector)
    end = style.index(until, start)
    return style[start:end]


def test_panel_tabs_are_generated_from_active_theme():
    try:
        set_theme("dark")
        dark = app_style()
        dark_bar = _rule(dark, 'QTabBar[panelTabs="true"] {', until='QTabBar[panelTabs="true"]::tab {')
        dark_selected = _rule(
            dark,
            'QTabBar[panelTabs="true"]::tab:selected {',
            until='QTabBar[panelTabs="true"]::tab:hover:!selected {',
        )
        assert f"background: {Theme.SURFACE};" in dark_bar
        assert f"background: {Theme.PANEL_3};" in dark_selected
        assert f"border-radius: {Theme.RADIUS_SM}px;" in dark_selected

        set_theme("light")
        light = app_style()
        light_bar = _rule(light, 'QTabBar[panelTabs="true"] {', until='QTabBar[panelTabs="true"]::tab {')
        light_selected = _rule(
            light,
            'QTabBar[panelTabs="true"]::tab:selected {',
            until='QTabBar[panelTabs="true"]::tab:hover:!selected {',
        )
        assert f"background: {Theme.SURFACE};" in light_bar
        assert f"background: {Theme.PANEL_3};" in light_selected
        assert f"border-radius: {Theme.RADIUS_SM}px;" in light_selected
        assert light != dark
    finally:
        set_theme("dark")


def test_light_borders_contrast_with_panel_fills():
    """Light-mode borders must not match panel background tokens (were invisible)."""
    try:
        set_theme("light")
        assert Theme.BORDER != Theme.PANEL_2
        assert Theme.BORDER != Theme.PANEL_3
        assert Theme.BORDER_SOFT != Theme.PANEL_2
        style = app_style()
        assert Theme.BORDER in style
        assert Theme.BORDER_SOFT in style
    finally:
        set_theme("dark")


def test_tooltip_and_splitter_rules_follow_theme_tokens():
    try:
        set_theme("dark")
        style = app_style()
        tooltip = _rule(style, "QToolTip {", until="QCheckBox, QRadioButton {")
        split_h = _rule(style, "QSplitter::handle:horizontal {", until="QSplitter::handle:vertical {")
        split_v = _rule(style, "QSplitter::handle:vertical {", until="QScrollBar:vertical {")
        assert f"background: {Theme.SURFACE};" in tooltip
        assert f"color: {Theme.TEXT_2};" in tooltip
        assert f"border: 1px solid {Theme.BORDER_SOFT};" in tooltip
        assert "padding: 3px 7px;" in tooltip
        assert "font-size: 11px;" in tooltip
        assert "margin: 0 1px;" in split_h
        assert "margin: 1px 0;" in split_v
    finally:
        set_theme("dark")
