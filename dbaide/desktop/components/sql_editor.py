"""SQL editor with keyword + schema autocomplete.

A QPlainTextEdit that pops a completer for SQL keywords and the live schema's
table/column names. Follows Qt's canonical completer pattern (the completer
filters navigation keys; we recompute the prefix after each keystroke).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QCompleter, QPlainTextEdit

from dbaide.rendering.sanitize import _SQL_KEYWORDS

_KEYWORDS = sorted({kw.upper() for kw in _SQL_KEYWORDS})


class SqlEditor(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = QStringListModel(list(_KEYWORDS), self)
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setWrapAround(False)
        self._completer.activated.connect(self._insert_completion)

    def set_completions(self, names: list[str]) -> None:
        """Merge schema identifiers (tables/columns) with the SQL keywords."""
        words = sorted(set(_KEYWORDS) | {str(n) for n in (names or []) if str(n).strip()})
        self._model.setStringList(words)

    # ── completion plumbing ─────────────────────────────────────────────────--

    def _current_prefix(self) -> str:
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        return tc.selectedText()

    def _insert_completion(self, completion: str) -> None:
        if self._completer.widget() is not self:
            return
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        tc.insertText(completion)
        self.setTextCursor(tc)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape,
            Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
        ):
            event.ignore()  # let the completer popup handle accept/navigate/dismiss
            return
        super().keyPressEvent(event)
        prefix = self._current_prefix()
        # Only pop up for word-ish prefixes of length ≥ 2 (avoid noise while typing
        # operators/whitespace).
        if len(prefix) < 2 or not (prefix[-1].isalnum() or prefix[-1] == "_"):
            popup.hide()
            return
        if prefix != self._completer.completionPrefix():
            self._completer.setCompletionPrefix(prefix)
            popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        if self._completer.completionCount() == 0:
            popup.hide()
            return
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24)
        self._completer.complete(rect)
