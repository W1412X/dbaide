"""Shows the SQL optimizer's suggestions (markdown) for the Workbench editor."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QLabel, QTextBrowser, QVBoxLayout

from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t


class SqlOptimizeDialog(QDialog):
    def __init__(self, suggestions: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("optimize.title"))
        self.resize(660, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QLabel(_t("optimize.subtitle"))
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        lay.addWidget(head)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setMarkdown(suggestions)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background:{Theme.SURFACE}; color:{Theme.TEXT};"
            f" border:1px solid {Theme.BORDER_SOFT}; border-radius:8px; padding:8px; font-size:13px; }}")
        lay.addWidget(self.view, 1)
