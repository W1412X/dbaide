"""DocTab — shows an asset-doc markdown for a schema node.

Opened (lazily) when the user single-clicks a schema node in Workbench mode.
The tab starts empty and is filled once the background asset-load finishes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from dbaide.desktop.components.markdown import MarkdownView


class DocTab(QWidget):
    """Shows an asset-doc markdown. Opened when the user single-clicks a schema node."""

    def __init__(self, title: str, markdown: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = MarkdownView()
        if markdown:
            self.view.append_card(title, markdown)
        layout.addWidget(self.view, 1)

    def set_content(self, title: str, markdown: str) -> None:
        self.view.clear_view()
        if markdown:
            self.view.append_card(title, markdown)
