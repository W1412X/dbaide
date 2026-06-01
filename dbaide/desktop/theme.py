"""Dark theme tokens and global Qt stylesheet for DBAide desktop."""

from __future__ import annotations


class Theme:
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


_INPUT = f"""
    background: {Theme.PANEL};
    color: {Theme.TEXT};
    border: 1px solid {Theme.BORDER};
    border-radius: 9px;
    min-height: 34px;
    max-height: 34px;
    selection-background-color: {Theme.PANEL_3};
"""

APP_STYLE = f"""
* {{
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI";
    color: {Theme.TEXT};
    font-size: 13px;
}}
QMainWindow, QWidget#root {{
    background: {Theme.BG};
}}
QDialog {{
    background: {Theme.BG};
    color: {Theme.TEXT};
}}
QFrame[panel="true"] {{
    background: {Theme.SURFACE};
    border: 1px solid {Theme.BORDER_SOFT};
    border-radius: 10px;
}}
QLabel[muted="true"] {{
    color: {Theme.MUTED};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding-left: 2px;
}}
QPushButton {{
    background: {Theme.PANEL_2};
    color: {Theme.TEXT_2};
    border: 1px solid {Theme.BORDER};
    border-radius: 9px;
    padding: 0px 16px;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton:hover {{
    background: {Theme.PANEL_3};
    color: {Theme.TEXT};
}}
QPushButton:disabled {{
    color: {Theme.MUTED_2};
    background: {Theme.PANEL};
}}
QPushButton[primary="true"] {{
    background: {Theme.ACCENT};
    color: {Theme.ACCENT_TEXT};
    border: 1px solid {Theme.ACCENT};
    font-weight: 600;
    padding: 0px 16px;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton[primary="true"]:hover {{
    background: {Theme.ACCENT_HOVER};
    border: 1px solid {Theme.ACCENT_HOVER};
    color: {Theme.ACCENT_TEXT};
}}
QPushButton[primary="true"]:disabled {{
    background: {Theme.PANEL_2};
    color: {Theme.MUTED_2};
    border: 1px solid {Theme.BORDER};
}}
QPushButton[tab="true"] {{
    border-radius: 8px 8px 0 0;
    border-bottom: 2px solid transparent;
    background: transparent;
}}
QPushButton[tab="true"][active="true"] {{
    color: {Theme.TEXT};
    border-bottom: 2px solid {Theme.BLUE};
    background: {Theme.PANEL_2};
}}
QLineEdit {{
    {_INPUT}
    padding: 0px 12px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {Theme.FOCUS};
}}
QComboBox {{
    {_INPUT}
    padding: 0px 28px 0px 12px;
}}
QComboBox QAbstractItemView {{
    background: {Theme.PANEL};
    color: {Theme.TEXT};
    selection-background-color: {Theme.PANEL_3};
    border: 1px solid {Theme.BORDER};
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
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}
QTextEdit, QTextBrowser, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {Theme.SURFACE};
    color: {Theme.TEXT};
    border: 1px solid {Theme.BORDER_SOFT};
    border-radius: 8px;
    selection-background-color: {Theme.PANEL_3};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea QWidget {{
    background: {Theme.BG};
}}
QLabel#formLabel {{
    background-color: rgba(0, 0, 0, 0);
    background: transparent;
    border: none;
    border-width: 0;
    border-radius: 0;
    color: {Theme.TEXT_2};
    font-size: 13px;
    font-weight: 400;
    padding: 0 10px 0 0;
    margin: 0;
}}
QTabWidget::pane {{
    border: 1px solid {Theme.BORDER_SOFT};
    border-radius: 8px;
    background: {Theme.SURFACE};
    top: -1px;
}}
QTabBar::tab {{
    background: {Theme.PANEL};
    color: {Theme.MUTED};
    padding: 8px 14px;
    border: 1px solid {Theme.BORDER_SOFT};
    border-bottom: none;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {Theme.SURFACE};
    color: {Theme.TEXT};
    border-bottom: 2px solid {Theme.BLUE};
}}
QHeaderView::section {{
    background: {Theme.PANEL_2};
    color: {Theme.TEXT_2};
    border: none;
    padding: 6px 8px;
    font-weight: 600;
}}
QSplitter::handle {{
    background: {Theme.BORDER_SOFT};
    width: 1px;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 5px 8px;
}}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {Theme.PANEL_3};
    color: {Theme.TEXT};
}}
QScrollBar:vertical {{
    background: {Theme.SURFACE};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {Theme.PANEL_3};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar:horizontal {{
    background: {Theme.SURFACE};
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {Theme.PANEL_3};
    border-radius: 4px;
    min-width: 24px;
}}
QToolButton {{
    background: {Theme.PANEL_2};
    color: {Theme.TEXT_2};
    border: 1px solid {Theme.BORDER};
    border-radius: 9px;
    padding: 0px 10px;
    min-height: 34px;
    max-height: 34px;
}}
QToolButton:hover {{
    background: {Theme.PANEL_3};
    color: {Theme.TEXT};
}}
QTabBar[segmented="true"]::tab {{
    background: {Theme.PANEL};
    color: {Theme.MUTED};
    padding: 7px 18px;
    border: 1px solid {Theme.BORDER_SOFT};
    margin-right: 0;
    min-width: 68px;
    max-height: 34px;
}}
QTabBar[segmented="true"]::tab:selected {{
    background: {Theme.PANEL_3};
    color: {Theme.TEXT};
    border: 1px solid {Theme.BORDER};
}}
QTabBar[segmented="true"]::tab:first {{
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
}}
QTabBar[segmented="true"]::tab:last {{
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QTabBar[panelTabs="true"]::tab {{
    background: transparent;
    color: {Theme.MUTED};
    padding: 4px 12px;
    border: none;
    margin: 0;
    min-height: 28px;
    max-height: 28px;
    font-size: 12px;
    font-weight: 500;
}}
QTabBar[panelTabs="true"]::tab:selected {{
    background: {Theme.PANEL_3};
    color: {Theme.TEXT};
    border-radius: 6px;
}}
QTabBar[panelTabs="true"]::tab:hover:!selected {{
    color: {Theme.TEXT_2};
    background: {Theme.PANEL_2};
    border-radius: 6px;
}}
QFrame[panelContent="true"] {{
    background: {Theme.SURFACE};
    border: 1px solid {Theme.BORDER_SOFT};
    border-radius: 10px;
}}
QStatusBar {{
    background: {Theme.BG};
    color: {Theme.MUTED};
    border-top: 1px solid {Theme.BORDER_SOFT};
}}
"""
