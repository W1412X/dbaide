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

from dbaide.rendering.sql_dialect import dialect_functions, dialect_keywords, normalize_dialect
from dbaide.desktop.theme import Theme

# Word(s) immediately before a trailing dot, e.g. "orders" or "analysis.orders".
# `[^\W\d]` is "a Unicode word char that isn't a digit" — i.e. a letter (incl. CJK)
# or underscore — so unquoted CJK-named tables/columns (e.g. "订单") still match
# without admitting digit-leading identifiers.
_QUALIFIED_DOT = re.compile(r"((?:[^\W\d][\w]*)(?:\.[^\W\d][\w]*)*)\.(\w*)$")

# Extract table aliases: FROM table [AS] alias, JOIN table [AS] alias, FROM a, b [AS] alias
_ALIAS_RE = re.compile(
    r"(?:FROM|JOIN|,)\s+"
    r"((?:[`\"]?[^\W\d]\w*[`\"]?)(?:\.[`\"]?[^\W\d]\w*[`\"]?)*)"
    r"\s+(?:AS\s+)?([^\W\d]\w*)",
    re.IGNORECASE,
)

# Keywords that should never be treated as aliases.
_NOT_ALIAS = frozenset({
    "where", "on", "and", "or", "not", "in", "is", "null", "like", "between",
    "set", "values", "into", "select", "from", "join", "left", "right", "inner",
    "outer", "cross", "full", "natural", "using", "order", "group", "having",
    "limit", "offset", "union", "except", "intersect", "as", "case", "when",
    "then", "else", "end", "exists", "asc", "desc", "distinct", "all", "any",
    "with", "recursive", "returning", "insert", "update", "delete", "create",
    "alter", "drop", "index", "table", "view", "true", "false", "if",
})


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
        self._tables_by_database: dict[str, list[str]] = {}
        self._columns_by_qualified: dict[str, list[str]] = {}
        self._qualified_tables: list[str] = []
        self._column_types: dict[str, str] = {}
        self._all_columns: list[str] = []
        self._dialect = "generic"
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
        # The completer popup is a top-level QListView that the global QSS doesn't
        # paint — without this it falls back to the system palette and mismatches the
        # theme (e.g. a dark popup in light mode). Style it to the current theme.
        popup = self._completer.popup()
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup.setStyleSheet(self._completer_popup_stylesheet())

        # Line-number gutter + current-line highlight (Qt CodeEditor pattern).
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_area_width()
        self._highlight_current_line()

    @staticmethod
    def _completer_popup_stylesheet() -> str:
        """Popup is top-level — include slim scrollbars (global QSS does not apply)."""
        return (
            f"QListView {{ background: {Theme.SURFACE}; color: {Theme.TEXT};"
            f" border: 1px solid {Theme.BORDER}; border-radius: 8px; padding: 4px;"
            f" outline: none; }}"
            f"QListView::item {{ padding: 4px 8px; border-radius: 5px; }}"
            f"QListView::item:selected {{ background: {Theme.PANEL_3}; color: {Theme.TEXT}; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: {Theme.PANEL_3}; border-radius: 3px;"
            f" min-height: 20px; border: none; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {Theme.MUTED_2}; }}"
            f"QScrollBar::handle:vertical:pressed {{ background: {Theme.MUTED}; }}"
            f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0;"
            f" background: none; border: none; }}"
            f"QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}"
        )

    def _keywords(self) -> list[str]:
        return sorted(dialect_keywords(self._dialect))

    def _functions(self) -> list[str]:
        return dialect_functions(self._dialect)

    def _general_words(self) -> list[str]:
        """Keywords + functions + schema identifiers (deduped, keywords last)."""
        ident = list(dict.fromkeys(
            self._databases + self._qualified_tables + self._tables + self._all_columns
        ))
        return ident + self._functions() + self._keywords()

    def _set_general_words(self) -> None:
        self._mode = "general"
        self._model.setStringList(self._general_words())

    def set_dialect(self, dialect: str) -> None:
        self._dialect = normalize_dialect(dialect)
        self._set_general_words()

    def set_schema(self, schema: dict) -> None:
        """Feed structured schema for context-aware completion.

        schema may include databases, tables, columns_by_table, tables_by_database,
        qualified_tables, columns_by_qualified, and column_types.
        """
        schema = schema or {}
        self._databases = [str(d) for d in (schema.get("databases") or []) if str(d).strip()]
        self._tables = sorted({str(t) for t in (schema.get("tables") or []) if str(t).strip()})
        self._columns_by_table = {
            str(t): [str(c) for c in (cols or [])]
            for t, cols in (schema.get("columns_by_table") or {}).items()
        }
        self._tables_by_database = {
            str(d): [str(t) for t in (ts or [])]
            for d, ts in (schema.get("tables_by_database") or {}).items()
        }
        self._qualified_tables = sorted({
            str(q) for q in (schema.get("qualified_tables") or []) if str(q).strip()
        })
        self._columns_by_qualified = {
            str(k): [str(c) for c in (cols or [])]
            for k, cols in (schema.get("columns_by_qualified") or {}).items()
        }
        self._column_types = {
            str(k): str(v) for k, v in (schema.get("column_types") or {}).items() if str(k).strip()
        }
        if schema.get("dialect"):
            self._dialect = normalize_dialect(str(schema.get("dialect")))
        self._all_columns = sorted({c for cols in self._columns_by_table.values() for c in cols})
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

    def _in_string(self) -> bool:
        """True when the cursor sits inside a SQL string literal ('…' or "…")."""
        text = self.toPlainText()[: self.textCursor().position()]
        in_single = in_double = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "'" and not in_double:
                if in_single and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            i += 1
        return in_single or in_double

    def _current_prefix(self) -> str:
        """Word fragment from word-start to cursor (not the full WordUnderCursor)."""
        tc = self.textCursor()
        pos = tc.positionInBlock()
        text = tc.block().text()[:pos]
        i = len(text) - 1
        while i >= 0 and (text[i].isalnum() or text[i] == "_"):
            i -= 1
        return text[i + 1 :]

    def _insert_completion(self, completion: str) -> None:
        if self._completer.widget() is not self:
            return
        token = str(completion or "").split(" · ", 1)[0].strip()
        if not token:
            return
        prefix = self._current_prefix()
        tc = self.textCursor()
        tc.movePosition(
            QTextCursor.MoveOperation.Left,
            QTextCursor.MoveMode.KeepAnchor,
            len(prefix),
        )
        tc.insertText(token)
        self.setTextCursor(tc)

    def _force_completion(self) -> None:
        if self._in_string():
            return
        tc = self.textCursor()
        before = tc.block().text()[: tc.positionInBlock()]
        scoped_words, scoped_mode = self._scoped_words(before)
        popup = self._completer.popup()
        if scoped_words is not None:
            self._mode = scoped_mode
            self._model.setStringList(scoped_words)
            prefix = self._current_prefix()
            self._completer.setCompletionPrefix(prefix)
            popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
            if self._completer.completionCount() > 0:
                self._popup_at_cursor(popup)
            return
        if self._mode != "general":
            self._set_general_words()
        prefix = self._current_prefix()
        self._completer.setCompletionPrefix(prefix or "")
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        if self._completer.completionCount() > 0:
            self._popup_at_cursor(popup)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape,
            Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return
        if event.key() == Qt.Key.Key_Slash and (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            self.toggle_comment()
            return
        if event.key() == Qt.Key.Key_Space and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._force_completion()
            return
        super().keyPressEvent(event)

        if self._in_string():
            popup.hide()
            return

        # Cascading context after a dot: "<table>." → that table's columns (also
        # handles "<db>.<table>." since we look at the word before the last dot);
        # "<db>." → that database's tables.
        tc = self.textCursor()
        before = tc.block().text()[: tc.positionInBlock()]
        scoped_words, scoped_mode = self._scoped_words(before)

        if scoped_words is not None:
            # Swap the model to the scoped words — completed even right after the dot
            # (empty prefix).
            self._mode = scoped_mode
            self._model.setStringList(scoped_words)
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

    def _column_labels(self, table_key: str) -> list[str]:
        cols = (
            self._columns_by_qualified.get(table_key)
            or self._columns_by_table.get(table_key.split(".")[-1], [])
        )
        labels: list[str] = []
        short = table_key.split(".")[-1]
        for col in cols:
            dtype = (
                self._column_types.get(f"{table_key}.{col}")
                or self._column_types.get(f"{short}.{col}")
                or ""
            )
            labels.append(f"{col} · {dtype}" if dtype else col)
        return labels

    def _parse_aliases(self) -> dict[str, str]:
        """Extract ``{alias_lower: table_ref}`` from the current SQL text."""
        aliases: dict[str, str] = {}
        for m in _ALIAS_RE.finditer(self.toPlainText()):
            table_ref = m.group(1).strip('`"')
            alias = m.group(2)
            if alias.lower() not in _NOT_ALIAS:
                aliases[alias.lower()] = table_ref
        return aliases

    def _scoped_words(self, before: str) -> tuple[list[str] | None, str]:
        """`<db>.` → tables; `<table>.` / `<db.table>.` → columns with types;
        `<alias>.` → aliased table's columns."""
        match = _QUALIFIED_DOT.search(before)
        if not match:
            return None, ""
        qualifier = match.group(1)
        if not qualifier:
            return None, ""

        if qualifier in self._columns_by_qualified:
            return self._column_labels(qualifier), f"qualified:{qualifier}"

        table = self._match_table(qualifier.split(".")[-1])
        if table is not None:
            key = qualifier if qualifier in self._columns_by_qualified else table
            return self._column_labels(key), f"table:{table}"

        if "." not in qualifier:
            db = self._match_database(qualifier)
            if db is not None:
                tables = self._tables_by_database.get(db) or self._tables
                return tables, f"db:{db}"

            alias_table = self._parse_aliases().get(qualifier.lower())
            if alias_table:
                real = self._match_table(alias_table.split(".")[-1])
                if real is not None:
                    key = alias_table if alias_table in self._columns_by_qualified else real
                    return self._column_labels(key), f"alias:{qualifier}"
        return None, ""

    def _match_table(self, word: str) -> str | None:
        """Case-insensitive lookup of a table name (the part before a dot)."""
        if not word:
            return None
        low = word.lower()
        for t in self._tables:
            if t.lower() == low:
                return t
        return None

    def _match_database(self, word: str) -> str | None:
        """Case-insensitive lookup of a database name (the part before a dot)."""
        if not word:
            return None
        low = word.lower()
        for d in self._databases:
            if d.lower() == low:
                return d
        return None

    def _popup_at_cursor(self, popup) -> None:
        rect = self.cursorRect()
        # Reserve a slim themed scrollbar width, not the native fat arrow bar.
        rect.setWidth(popup.sizeHintForColumn(0) + 12)
        self._completer.complete(rect)
