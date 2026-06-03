"""SQL editor with line numbers, current-line highlight, comment toggle, and
keyword + schema autocomplete.

A QPlainTextEdit that pops a completer for SQL keywords and the live schema's
table/column names (Qt's canonical completer pattern), with a line-number gutter
and current-line highlight (Qt's canonical CodeEditor pattern). ⌘/ toggles line
comments on the selection.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import QRect, QSize, Qt, QStringListModel
from PyQt6.QtGui import QColor, QPainter, QTextCursor, QTextFormat
from PyQt6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QWidget

from dbaide.desktop.theme import Theme
from dbaide.rendering.sanitize import _SQL_KEYWORDS

_KEYWORDS = sorted({kw.upper() for kw in _SQL_KEYWORDS})

# Word immediately before a trailing dot, e.g. the "orders" in "orders.cit".
_DOT_PREFIX = re.compile(r"([A-Za-z_][\w]*)\.\w*$")


class _LineNumberArea(QWidget):
    def __init__(self, editor: "SqlEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt signature)
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self._editor.line_number_area_paint_event(event)


class SqlEditor(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Structured schema for context-aware completion.
        self._databases: list[str] = []
        self._tables: list[str] = []
        self._columns_by_table: dict[str, list[str]] = {}
        self._all_columns: list[str] = []
        # A single QStringListModel holds the active completion words; we swap its
        # string list for the general vocabulary vs. a table's columns. (Plain string
        # model — robust across the widget lifecycle; richer item models proved
        # crash-prone on teardown across rapid window create/destroy cycles.)
        self._model = QStringListModel(self)
        self._set_general_words()
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setWrapAround(False)
        self._completer.activated.connect(self._insert_completion)

        # Line-number gutter + current-line highlight (Qt CodeEditor pattern).
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_area_width()
        self._highlight_current_line()

    def _general_words(self) -> list[str]:
        """The general completion vocabulary: keywords + databases + tables + all
        columns (deduped, keywords last so identifiers rank first)."""
        ident = list(dict.fromkeys(self._databases + self._tables + self._all_columns))
        return ident + _KEYWORDS

    def _set_general_words(self) -> None:
        self._mode = "general"
        self._model.setStringList(self._general_words())

    def set_schema(self, schema: dict) -> None:
        """Feed structured schema for context-aware completion.

        schema = {"databases": [...], "tables": [...],
                  "columns_by_table": {table: [col, ...]}}
        """
        schema = schema or {}
        self._databases = [str(d) for d in (schema.get("databases") or []) if str(d).strip()]
        self._tables = sorted({str(t) for t in (schema.get("tables") or []) if str(t).strip()})
        self._columns_by_table = {
            str(t): [str(c) for c in (cols or [])]
            for t, cols in (schema.get("columns_by_table") or {}).items()
        }
        self._all_columns = sorted({c for cols in self._columns_by_table.values() for c in cols})
        self._set_general_words()

    def set_completions(self, names: list[str]) -> None:
        """Back-compat: a flat identifier list (treated as tables + columns)."""
        names = [str(n) for n in (names or []) if str(n).strip()]
        self.set_schema({"tables": names, "columns_by_table": {}})
        self._all_columns = sorted(set(names))
        self._set_general_words()

    def completion_names(self) -> list[str]:
        """The active general completion vocabulary (for tests/introspection)."""
        return self._general_words()

    # ── line-number gutter ──────────────────────────────────────────────────--

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_area_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_area(self, rect, dy: int) -> None:
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_area_width()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor(Theme.PANEL))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor(Theme.MUTED_2))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._line_area.width() - 6,
                                 self.fontMetrics().height(),
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    def _highlight_current_line(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(Theme.PANEL_2))
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            cursor = self.textCursor()
            cursor.clearSelection()
            sel.cursor = cursor
            selections.append(sel)
        self.setExtraSelections(selections)

    # ── comment toggle ──────────────────────────────────────────────────────--

    def toggle_comment(self) -> None:
        """Toggle a leading ``-- `` on each line spanned by the selection."""
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.setPosition(start)
        start_block = cursor.blockNumber()
        cursor.setPosition(end)
        end_block = cursor.blockNumber()

        doc = self.document()
        lines = [doc.findBlockByNumber(n) for n in range(start_block, end_block + 1)]
        # If every non-blank line is already commented, uncomment; else comment.
        all_commented = all(b.text().lstrip().startswith("--") for b in lines if b.text().strip())

        cursor.beginEditBlock()
        for block in lines:
            text = block.text()
            bc = QTextCursor(block)
            bc.select(QTextCursor.SelectionType.LineUnderCursor)
            if all_commented:
                stripped = text.lstrip()
                if stripped.startswith("--"):
                    indent = text[: len(text) - len(stripped)]
                    rest = stripped[2:]
                    if rest.startswith(" "):
                        rest = rest[1:]
                    bc.insertText(indent + rest)
            else:
                if text.strip():
                    indent_len = len(text) - len(text.lstrip())
                    bc.insertText(text[:indent_len] + "-- " + text[indent_len:])
        cursor.endEditBlock()

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
        # ⌘/ or Ctrl+/ — toggle line comments.
        if event.key() == Qt.Key.Key_Slash and (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            self.toggle_comment()
            return
        super().keyPressEvent(event)

        # Context: are we completing columns of "<table>." ?
        tc = self.textCursor()
        before = tc.block().text()[: tc.positionInBlock()]
        dot = _DOT_PREFIX.search(before)
        dot_table = self._match_table(dot.group(1)) if dot else None

        if dot_table is not None:
            # Swap the model's words to that table's columns — completed even right
            # after the dot (empty prefix).
            self._mode = dot_table
            self._model.setStringList(self._columns_by_table.get(dot_table, []))
            prefix = self._current_prefix()
            self._completer.setCompletionPrefix(prefix)
            popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
            if self._completer.completionCount() == 0:
                popup.hide()
                return
            self._popup_at_cursor(popup)
            return

        # General completion: keywords + db + tables + columns.
        if self._mode != "general":
            self._set_general_words()
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
        self._popup_at_cursor(popup)

    def _match_table(self, word: str) -> str | None:
        """Case-insensitive lookup of a table name (the part before a dot)."""
        if not word:
            return None
        low = word.lower()
        for t in self._tables:
            if t.lower() == low:
                return t
        return None

    def _popup_at_cursor(self, popup) -> None:
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 40)
        self._completer.complete(rect)
