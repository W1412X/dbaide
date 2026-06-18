"""Lightweight SQL syntax highlighter for the SQL editor."""

from __future__ import annotations

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from dbaide.rendering.sql_dialect import dialect_functions, dialect_keywords, normalize_dialect
from dbaide.desktop.theme import Theme


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        f.setFontItalic(True)
    return f


class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, document, *, dialect: str = "generic") -> None:
        super().__init__(document)
        self._dialect = normalize_dialect(dialect)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._string_rules: list[QRegularExpression] = []
        self._string_fmt = _fmt(Theme.GREEN)
        self._comment_fmt = _fmt(Theme.MUTED, italic=True)
        self._function_fmt = _fmt(Theme.TEXT_2, bold=True)
        self._operator_fmt = _fmt(Theme.YELLOW)
        self._identifier_fmt = _fmt(Theme.TEXT)
        self._block_start = QRegularExpression(r"/\*")
        self._block_end = QRegularExpression(r"\*/")
        self._rebuild_rules()

    def set_dialect(self, dialect: str) -> None:
        name = normalize_dialect(dialect)
        if name == self._dialect:
            return
        self._dialect = name
        self._rebuild_rules()
        self.rehighlight()

    def _rebuild_rules(self) -> None:
        ci = QRegularExpression.PatternOption.CaseInsensitiveOption
        kws = sorted(dialect_keywords(self._dialect), key=len, reverse=True)
        funcs = sorted(dialect_functions(self._dialect), key=len, reverse=True)
        self._rules = [
            (QRegularExpression(r"\b(?:" + "|".join(kws) + r")\b", ci), _fmt(Theme.BLUE, bold=True)),
        ]
        if funcs:
            self._rules.append(
                (QRegularExpression(r"\b(?:" + "|".join(funcs) + r")\s*(?=\()", ci), self._function_fmt)
            )
        self._rules.extend([
            (QRegularExpression(r"\b\d+(?:\.\d+)?\b"), _fmt(Theme.YELLOW)),
            (QRegularExpression(r"(::|\|\||&&|<>|!=|<=|>=|:=)"), self._operator_fmt),
        ])
        self._string_rules = [
            QRegularExpression(r"'(?:[^']|'')*'"),
            QRegularExpression(r'"(?:[^"]|"")*"'),
        ]
        if self._dialect == "mysql":
            self._string_rules.append(QRegularExpression(r"`(?:[^`]|``)*`"))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt signature)
        for rx, fmt in self._rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        # Collect string literal spans so line-comment markers inside strings
        # (e.g. '--' in 'foo--bar') are not treated as comments.
        string_spans: list[tuple[int, int]] = []
        for rx in self._string_rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), self._string_fmt)
                string_spans.append((m.capturedStart(), m.capturedEnd()))
        # Find the first comment marker that is OUTSIDE a string — scanning past any
        # earlier marker that sits inside a literal (e.g. the '--' in 'a--b' must not
        # hide the real '-- comment' that follows it).
        dash = self._first_marker_outside_strings(text, "--", string_spans)
        if dash >= 0:
            self.setFormat(dash, len(text) - dash, self._comment_fmt)
        if self._dialect == "mysql":
            hash_at = self._first_marker_outside_strings(text, "#", string_spans, require_boundary=True)
            if hash_at >= 0:
                self.setFormat(hash_at, len(text) - hash_at, self._comment_fmt)
        self._highlight_block_comments(text)

    @staticmethod
    def _inside_string(pos: int, spans: list[tuple[int, int]]) -> bool:
        return any(s <= pos < e for s, e in spans)

    @classmethod
    def _first_marker_outside_strings(
        cls, text: str, marker: str, spans: list[tuple[int, int]], *, require_boundary: bool = False
    ) -> int:
        pos = text.find(marker)
        while pos >= 0:
            boundary_ok = (not require_boundary) or pos == 0 or text[pos - 1].isspace()
            if boundary_ok and not cls._inside_string(pos, spans):
                return pos
            pos = text.find(marker, pos + 1)
        return -1

    def _highlight_block_comments(self, text: str) -> None:
        # Default to "not inside a block comment" so that Qt detects the state
        # transition when a previously-comment block is edited back to normal code.
        self.setCurrentBlockState(0)
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
