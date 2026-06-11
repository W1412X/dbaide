"""Theme tokens and global Qt stylesheet for DBAide desktop.

Supports dark (default) and light themes. The module-level ``Theme`` reference
always points at the active palette class; call ``set_theme("light")`` or
``set_theme("dark")`` to switch. ``app_style()`` returns the full QSS string
using the *current* Theme at call time so it can be re-applied after a switch.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


# ── Palette classes ──────────────────────────────────────────────────────────

class _DarkTheme:
    BG = "#07080a"
    SURFACE = "#0d0f12"
    PANEL = "#111419"
    PANEL_2 = "#151922"
    PANEL_3 = "#1b202b"
    BORDER = "#252b36"
    BORDER_SOFT = "#1b2026"
    TEXT = "#eef1f5"
    TEXT_2 = "#b7bec9"
    MUTED = "#737b89"
    MUTED_2 = "#515865"
    ACCENT = "#3b82f6"
    ACCENT_HOVER = "#5b9bff"
    ACCENT_TEXT = "#ffffff"
    BLUE = "#67a7ff"
    FOCUS = "#3b82f6"
    GREEN = "#55c985"
    YELLOW = "#e9c46a"
    RED = "#ff6b6b"
    CODE_BG = "#090b0f"
    NULL = "#515865"
    RADIUS_SM = 6
    RADIUS_MD = 8
    RADIUS_LG = 10
    RADIUS_XL = 12


class _LightTheme:
    BG = "#ffffff"
    SURFACE = "#f8f9fa"
    PANEL = "#f1f3f5"
    PANEL_2 = "#e9ecef"
    PANEL_3 = "#dee2e6"
    # Slightly darker than panel fills — visible but not heavy (between old invisible & prior fix).
    BORDER = "#c9cfd6"
    BORDER_SOFT = "#d8dde3"
    TEXT = "#1a1a2e"
    TEXT_2 = "#495057"
    MUTED = "#868e96"
    MUTED_2 = "#adb5bd"
    ACCENT = "#3b82f6"
    ACCENT_HOVER = "#2563eb"
    ACCENT_TEXT = "#ffffff"
    BLUE = "#3b82f6"
    FOCUS = "#3b82f6"
    GREEN = "#22c55e"
    YELLOW = "#eab308"
    RED = "#ef4444"
    CODE_BG = "#f1f3f5"
    NULL = "#adb5bd"
    RADIUS_SM = 6
    RADIUS_MD = 8
    RADIUS_LG = 10
    RADIUS_XL = 12


# ── Theme accessor / switcher ────────────────────────────────────────────────
#
# CRITICAL: ``Theme`` must be a single, stable object whose *attributes* are
# rewritten in place by ``set_theme()`` — NOT a reference swapped between two
# classes. Almost every module does ``from dbaide.desktop.theme import Theme``,
# which captures the object at import time; reassigning the module global would
# leave all those captured references pointing at the old palette (so inline
# ``Theme.PANEL`` stylesheets would stay dark in light mode). Mutating one shared
# object's attributes means every reference sees the switch.

_THEMES: dict[str, type] = {"dark": _DarkTheme, "light": _LightTheme}
_COLOR_KEYS = [k for k in vars(_DarkTheme) if k.isupper()]


class _Palette:
    """Mutable holder for the active palette's colors (a stable singleton)."""
    pass


Theme = _Palette()
_active_name = "dark"

# Seed the dark palette immediately so ``Theme.*`` is usable at import time
# (before set_theme / _regenerate_icons exist). The launcher calls set_theme()
# with the saved preference once the module is fully loaded.
for _k in _COLOR_KEYS:
    setattr(Theme, _k, getattr(_DarkTheme, _k))


def set_theme(name: str) -> None:
    """Switch the active theme by copying the chosen palette's colors onto the
    shared ``Theme`` object in place. Call ``app_style()`` afterwards to get the
    updated QSS and re-apply it. (Inline per-widget styles only pick up the change
    for widgets created *after* this call, so a live switch needs a restart.)"""
    global _active_name
    src = _THEMES.get(name, _DarkTheme)
    _active_name = "light" if src is _LightTheme else "dark"
    for key in _COLOR_KEYS:
        setattr(Theme, key, getattr(src, key))
    # Regenerate icon files so SVG stroke colors match the new palette.
    _regenerate_icons()


def current_theme_name() -> str:
    return _active_name


# ── Icon materialisation ────────────────────────────────────────────────────
# Glyphs for native controls (checkbox tick, combo/spinbox chevrons). Each is
# materialised from embedded SVG to a temp file (rather than a shipped asset)
# so the QSS url() resolves identically in dev, installed wheels, and frozen
# PyInstaller builds. POSIX paths so url() works on every platform.


def _icon_svgs() -> dict[str, str]:
    """Build SVG strings using the *current* Theme palette."""
    check_color = "#ffffff"  # always white on the accent background
    chevron_color = Theme.TEXT_2
    close_color = Theme.MUTED
    return {
        "check": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<path d="M3.5 8.5 L6.5 11.5 L12.5 5" fill="none" stroke="{check_color}" stroke-width="2"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chevron-down": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<path d="M4 6.5 L8 10.5 L12 6.5" fill="none" stroke="{chevron_color}" stroke-width="1.6"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chevron-up": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<path d="M4 9.5 L8 5.5 L12 9.5" fill="none" stroke="{chevron_color}" stroke-width="1.6"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "close": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<path d="M4.5 4.5 L11.5 11.5 M11.5 4.5 L4.5 11.5" fill="none" stroke="{close_color}"'
            ' stroke-width="1.5" stroke-linecap="round"/></svg>'
        ),
        "menu-check": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<path d="M3.5 8.5 L6.5 11.5 L12.5 5" fill="none" stroke="{chevron_color}" stroke-width="2"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }


def _materialize_icons() -> dict[str, str]:
    paths: dict[str, str] = {}
    try:
        icon_dir = Path(tempfile.gettempdir()) / "dbaide-icons"
        icon_dir.mkdir(exist_ok=True)
        for name, svg in _icon_svgs().items():
            path = icon_dir / f"{name}.svg"
            # Always overwrite — the theme may have changed.
            if not path.exists() or path.read_text() != svg:
                path.write_text(svg)
            paths[name] = path.as_posix()
    except OSError:
        pass  # no writable temp -> controls fall back to no-glyph states
    return paths


def _regenerate_icons() -> None:
    """Re-materialise icon files after a theme switch."""
    global _ICONS, _CHECK_ICON, _CHEVRON_DOWN, _CHEVRON_UP, _CLOSE_ICON, _MENU_CHECK_ICON
    _ICONS = _materialize_icons()
    _CHECK_ICON = _ICONS.get("check", "")
    _CHEVRON_DOWN = _ICONS.get("chevron-down", "")
    _CHEVRON_UP = _ICONS.get("chevron-up", "")
    _CLOSE_ICON = _ICONS.get("close", "")
    _MENU_CHECK_ICON = _ICONS.get("menu-check", "")


_ICONS = _materialize_icons()
_CHECK_ICON = _ICONS.get("check", "")
_CHEVRON_DOWN = _ICONS.get("chevron-down", "")
_CHEVRON_UP = _ICONS.get("chevron-up", "")
_CLOSE_ICON = _ICONS.get("close", "")
_MENU_CHECK_ICON = _ICONS.get("menu-check", "")


# ── Stylesheet ───────────────────────────────────────────────────────────────

def app_style() -> str:
    """Return the full application QSS using the *current* ``Theme``."""
    T = Theme  # local alias for brevity

    _INPUT = f"""
        background: {T.PANEL};
        color: {T.TEXT};
        border: 1px solid {T.BORDER};
        border-radius: 9px;
        min-height: 26px;
        max-height: 26px;
        selection-background-color: {T.PANEL_3};
    """

    return f"""
* {{
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI";
    color: {T.TEXT};
    font-size: 13px;
    outline: none;  /* kill the native focus ring/box that reads as an extra border */
}}
QMainWindow, QWidget#root, QDialog {{
    background-color: {T.BG};
}}
QWidget#topBar {{
    background-color: {T.BG};
    border: none;
}}
QFrame[panel="true"] {{
    background: {T.SURFACE};
    border: 1px solid {T.BORDER_SOFT};
    border-radius: 10px;
}}
QLabel[muted="true"] {{
    color: {T.MUTED};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding-left: 2px;
}}
QPushButton {{
    background: {T.PANEL_2};
    color: {T.TEXT_2};
    border: 1px solid {T.BORDER};
    border-radius: 9px;
    padding: 0px 10px;
    min-height: 26px;
    max-height: 26px;
}}
QPushButton:hover {{
    background: {T.PANEL_3};
    color: {T.TEXT};
}}
QPushButton:disabled {{
    color: {T.MUTED_2};
    background: {T.PANEL};
}}
QPushButton[primary="true"] {{
    background: {T.ACCENT};
    color: {T.ACCENT_TEXT};
    border: 1px solid {T.ACCENT};
    font-weight: 600;
    padding: 0px 10px;
    min-height: 26px;
    max-height: 26px;
}}
QPushButton[primary="true"]:hover {{
    background: {T.ACCENT_HOVER};
    border: 1px solid {T.ACCENT_HOVER};
    color: {T.ACCENT_TEXT};
}}
QPushButton[primary="true"]:disabled {{
    background: {T.PANEL_2};
    color: {T.MUTED_2};
    border: 1px solid {T.BORDER};
}}
QPushButton:focus-visible, QToolButton:focus-visible {{
    border: 1px solid {T.FOCUS};
}}
QToolButton#modeSwitchButton:focus-visible {{
    border: 1px solid {T.FOCUS};
}}
QTabBar[panelTabs="true"]::tab:focus {{
    border: 1px solid {T.FOCUS};
}}
QListWidget::item:focus {{
    outline: none;
    background: {T.PANEL_2};
}}
QPushButton[tab="true"] {{
    border-radius: 8px 8px 0 0;
    border-bottom: 2px solid transparent;
    background: transparent;
}}
QPushButton[tab="true"][active="true"] {{
    color: {T.TEXT};
    border-bottom: 2px solid {T.BLUE};
    background: {T.PANEL_2};
}}
QLineEdit {{
    {_INPUT}
    padding: 0px 12px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border: 1px solid {T.FOCUS};
}}
QComboBox:focus {{
    border: 1px solid {T.FOCUS};
    outline: none;
}}
QComboBox {{
    {_INPUT}
    padding: 0px 28px 0px 12px;
    outline: none;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({_CHEVRON_DOWN});
    width: 14px;
    height: 14px;
}}
/* Lighter topbar selectors — kept for settings/forms; top bar uses PillSelect now. */
QComboBox[soft="true"] {{
    background: transparent;
    border: 1px solid {T.BORDER_SOFT};
    border-radius: {T.RADIUS_MD}px;
    outline: none;
}}
QComboBox[soft="true"]:hover {{
    background: {T.PANEL_2};
    border: 1px solid {T.BORDER};
}}
QComboBox[soft="true"]:focus {{
    background: {T.PANEL_2};
    border: 1px solid {T.BORDER};
    outline: none;
}}
QComboBox[soft="true"]::drop-down {{
    width: 22px;
    border: none;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background: {T.PANEL};
    color: {T.TEXT};
    selection-background-color: {T.PANEL_3};
    border: 1px solid {T.BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: 28px;
    padding: 4px 10px;
    border-radius: 4px;
}}
QSpinBox {{
    {_INPUT}
    padding: 0px 12px;
    padding-right: 24px;
}}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    border-top-right-radius: 9px;
    background: transparent;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border: none;
    border-bottom-right-radius: 9px;
    background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {T.PANEL_3};
}}
QSpinBox::up-arrow {{
    image: url({_CHEVRON_UP});
    width: 12px;
    height: 12px;
}}
QSpinBox::down-arrow {{
    image: url({_CHEVRON_DOWN});
    width: 12px;
    height: 12px;
}}
QTextEdit, QTextBrowser, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {T.SURFACE};
    color: {T.TEXT};
    border: 1px solid {T.BORDER_SOFT};
    border-radius: 8px;
    selection-background-color: {T.PANEL_3};
    outline: none;  /* no native focus ring/box on item views */
}}
QListView, QTreeView, QTableView, QAbstractItemView {{
    outline: none;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea QWidget {{
    background: {T.BG};
}}
QLabel#formLabel {{
    background-color: rgba(0, 0, 0, 0);
    background: transparent;
    border: none;
    border-width: 0;
    border-radius: 0;
    color: {T.TEXT_2};
    font-size: 13px;
    font-weight: 400;
    padding: 0 10px 0 0;
    margin: 0;
}}
QTabWidget::pane {{
    border: 1px solid {T.BORDER_SOFT};
    border-radius: 8px;
    background: {T.SURFACE};
    top: -1px;
}}
QTabWidget {{
    background: {T.SURFACE};
}}
QTabWidget::tab-bar {{
    background: {T.SURFACE};
}}
QTabBar::tab {{
    background: {T.PANEL};
    color: {T.MUTED};
    padding: 6px 12px;
    border: 1px solid {T.BORDER_SOFT};
    border-bottom: none;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {T.SURFACE};
    color: {T.TEXT};
    border-bottom: 2px solid {T.BLUE};
}}
QHeaderView::section {{
    background: {T.PANEL_2};
    color: {T.TEXT_2};
    border: none;
    padding: 4px 10px;
    font-weight: 600;
}}
QSplitter::handle {{
    background: {T.BORDER_SOFT};
    width: 1px;
}}
QSplitter::handle:hover {{
    background: {T.BORDER};
}}
QSplitter::handle:pressed {{
    background: {T.ACCENT};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 3px 6px;
}}
QTreeWidget::item:hover {{
    background: {T.PANEL_2};
}}
QTreeWidget::item:selected {{
    background: {T.PANEL_3};
    color: {T.TEXT};
}}
/* Lists are always single-column, so a rounded hover/selection reads cleanly
   (unlike the multi-column trace tree, which keeps square full-row highlights). */
QListWidget::item {{
    border-radius: 6px;
    margin: 1px 0;
}}
QListWidget::item:hover {{
    background: {T.PANEL_2};
}}
QListWidget::item:selected {{
    background: {T.PANEL_3};
    color: {T.TEXT};
}}
/* Slim, floating scrollbars: transparent track, rounded handle that brightens on
   hover, no arrow buttons -- matches the rest of the chrome. */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {T.PANEL_3};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {T.MUTED_2};
}}
QScrollBar::handle:vertical:pressed {{
    background: {T.MUTED};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {T.PANEL_3};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {T.MUTED_2};
}}
QScrollBar::handle:horizontal:pressed {{
    background: {T.MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QAbstractScrollArea::corner {{
    background: transparent;
}}
QToolButton {{
    background: {T.PANEL_2};
    color: {T.TEXT_2};
    border: 1px solid {T.BORDER};
    border-radius: 9px;
    padding: 0px 10px;
    min-height: 26px;
    max-height: 26px;
}}
QToolButton:hover {{
    background: {T.PANEL_3};
    color: {T.TEXT};
}}
QTabBar[segmented="true"]::tab {{
    background: {T.PANEL};
    color: {T.MUTED};
    padding: 5px 14px;
    border: 1px solid {T.BORDER_SOFT};
    margin-right: 0;
    min-width: 68px;
    max-height: 26px;
}}
QTabBar[segmented="true"]::tab:selected {{
    background: {T.PANEL_3};
    color: {T.TEXT};
    border: 1px solid {T.BORDER};
}}
QTabBar[segmented="true"]::tab:first {{
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
}}
QTabBar[segmented="true"]::tab:last {{
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QTabBar[sidebarSwitch="true"] {{
    background: {T.PANEL};
    border: none;
    border-radius: 10px;
    padding: 3px;
}}
QTabBar[sidebarSwitch="true"]::tab {{
    background: transparent;
    color: {T.MUTED};
    padding: 5px 10px;
    border: none;
    border-radius: 7px;
    margin: 0;
    min-width: 72px;
    min-height: 22px;
    max-height: 22px;
    font-weight: 650;
}}
QTabBar[sidebarSwitch="true"]::tab:selected {{
    background: {T.PANEL_2};
    color: {T.TEXT};
    border: none;
}}
QTabBar[sidebarSwitch="true"]::tab:hover:!selected {{
    background: {T.SURFACE};
    color: {T.TEXT_2};
}}
QTabBar[panelTabs="true"] {{
    /* Theme the bar AREA itself (behind/around the tabs). SURFACE (the content
       surface) — not the near-black BG — so the bar blends with the editor/results
       below instead of reading as a stark dark band. Also stops the native macOS
       style painting a system-appearance strip. */
    background: {T.SURFACE};
}}
QTabBar[panelTabs="true"]::tab {{
    background: transparent;
    color: {T.MUTED};
    padding: 0 10px;
    border: none;
    margin: 0;
    /* Reserve room for a short label + the close button so closable tabs like
       "Query 1" aren't clipped to "Quer…" (padding + the 16px close button + its
       margins eat ~42px before any text). */
    min-width: 90px;
    min-height: 24px;
    max-height: 24px;
    font-size: 12px;
    font-weight: 500;
}}
QTabBar[panelTabs="true"]::tab:selected {{
    background: {T.PANEL_3};
    color: {T.TEXT};
    border-radius: {T.RADIUS_SM}px;
}}
QTabBar[panelTabs="true"]::tab:hover:!selected {{
    color: {T.TEXT_2};
    background: {T.PANEL_2};
    border-radius: {T.RADIUS_SM}px;
}}
QTabBar[panelTabs="true"]::close-button {{
    image: url({_CLOSE_ICON});
    subcontrol-position: right;
    width: 16px;
    height: 16px;
    margin: 0 2px 0 4px;
    border-radius: 4px;
}}
QTabBar[panelTabs="true"]::close-button:hover {{
    background: {T.PANEL};
}}
QFrame[panelContent="true"] {{
    background: {T.SURFACE};
    border: 1px solid {T.BORDER_SOFT};
    border-radius: 10px;
}}
QStatusBar {{
    background: {T.BG};
    color: {T.MUTED};
    border-top: 1px solid {T.BORDER_SOFT};
}}
/* Tooltip -- the native one is a light box that clashes with the chrome. */
QToolTip {{
    background: {T.PANEL_3};
    color: {T.TEXT};
    border: 1px solid {T.BORDER};
    border-radius: 6px;
    padding: 4px 8px;
}}
/* Themed checkboxes / radios -- without this they fall back to the native platform
   control, which clashes with the dark chrome. Checked = filled accent. */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {T.TEXT_2};
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    background: {T.PANEL};
    border: 1px solid {T.BORDER};
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {T.MUTED};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {T.ACCENT};
    border-color: {T.ACCENT};
    image: url({_CHECK_ICON});
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {T.PANEL_2};
    border-color: {T.BORDER_SOFT};
}}
"""


def combo_popup_stylesheet() -> str:
    """List popup for ``Combo`` — opaque, rounded; matches global QComboBox item view rules."""
    T = Theme
    return f"""
    QAbstractItemView {{
        background-color: {T.PANEL};
        color: {T.TEXT};
        selection-background-color: {T.PANEL_3};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD}px;
        padding: 4px;
        outline: none;
    }}
    QAbstractItemView::item {{
        min-height: 28px;
        padding: 4px 10px;
        border-radius: 4px;
    }}
    """


def menu_stylesheet() -> str:
    """Popup menu QSS using the *current* theme (re-read on every open)."""
    T = Theme
    check = _MENU_CHECK_ICON or _CHECK_ICON
    return f"""
    QMenu {{
        background-color: {T.PANEL};
        color: {T.TEXT};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_LG}px;
        padding: 6px;
    }}
    QMenu::item {{
        background: transparent;
        padding: 8px 28px 8px 14px;
        border-radius: {T.RADIUS_SM}px;
        min-height: 20px;
    }}
    QMenu::item:selected {{
        background: {T.PANEL_3};
        color: {T.TEXT};
    }}
    QMenu::item:disabled {{
        color: {T.MUTED_2};
    }}
    QMenu::separator {{
        height: 1px;
        background: {T.BORDER_SOFT};
        margin: 4px 8px;
    }}
    QMenu::indicator {{
        width: 16px;
        height: 16px;
        margin-left: 2px;
        background: transparent;
    }}
    QMenu::indicator:checked {{
        image: url({check});
        background: transparent;
    }}
    QMenu::right-arrow {{
        width: 8px;
        height: 8px;
        margin-right: 8px;
    }}
    """


def workbench_tab_stylesheet(*, bordered_pane: bool = False) -> str:
    """Shared QTabWidget chrome for Workbench document tabs and SQL result panes."""
    T = Theme
    if bordered_pane:
        pane = (
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS_LG}px;"
            f" top: -1px; background: {T.SURFACE};"
        )
    else:
        pane = f"border: none; background: {T.SURFACE};"
    return (
        f"QTabWidget {{ background: {T.SURFACE}; }}"
        f"QTabWidget::tab-bar {{ background: {T.SURFACE}; }}"
        f"QTabWidget::pane {{ {pane} }}"
    )

