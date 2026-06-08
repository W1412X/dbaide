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
