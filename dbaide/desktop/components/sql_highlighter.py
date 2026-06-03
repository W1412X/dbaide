"""Lightweight SQL syntax highlighter for the SQL editor.

Colours keywords, string/number literals, and `--` / `/* */` comments using the
app theme. Keyword list is shared with the SQL guard so it stays in sync.
"""
from __future__ import annotations

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from dbaide.desktop.theme import Theme
from dbaide.rendering.sanitize import _SQL_KEYWORDS


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        f.setFontItalic(True)
    return f


class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        ci = QRegularExpression.PatternOption.CaseInsensitiveOption

        kws = sorted(_SQL_KEYWORDS, key=len, reverse=True)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = [
            (QRegularExpression(r"\b(?:" + "|".join(kws) + r")\b", ci), _fmt(Theme.BLUE, bold=True)),
            (QRegularExpression(r"\b\d+(?:\.\d+)?\b"), _fmt(Theme.YELLOW)),
        ]
        # Strings and comments are applied last so they win over keyword/number rules.
        self._string_rules = [
            QRegularExpression(r"'(?:[^']|'')*'"),
            QRegularExpression(r'"(?:[^"]|"")*"'),
        ]
        self._string_fmt = _fmt(Theme.GREEN)
        self._comment_fmt = _fmt(Theme.MUTED, italic=True)
        self._block_start = QRegularExpression(r"/\*")
        self._block_end = QRegularExpression(r"\*/")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt signature)
        for rx, fmt in self._rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        for rx in self._string_rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), self._string_fmt)
        # Line comments (-- …) win over everything to the end of the line.
        dash = text.find("--")
        if dash >= 0:
            self.setFormat(dash, len(text) - dash, self._comment_fmt)
        self._highlight_block_comments(text)

    def _highlight_block_comments(self, text: str) -> None:
        # State 1 = "inside a /* */ comment that started on a previous line".
        start = 0
        if self.previousBlockState() != 1:
            m = self._block_start.match(text)
            start = m.capturedStart() if m.hasMatch() else -1
        while start >= 0:
            end_m = self._block_end.match(text, start)
            if end_m.hasMatch():
                length = end_m.capturedEnd() - start
                self.setCurrentBlockState(0)
            else:
                length = len(text) - start
                self.setCurrentBlockState(1)
            self.setFormat(start, length, self._comment_fmt)
            nxt = self._block_start.match(text, start + length)
            start = nxt.capturedStart() if nxt.hasMatch() else -1
