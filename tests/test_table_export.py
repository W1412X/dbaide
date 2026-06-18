"""Result export formatters: JSON and INSERT (CSV/Markdown already exist),
plus sanitizer hardening tests."""
from dbaide.rendering.table import export_insert, export_json, export_markdown_table


def test_export_json_array_of_objects():
    import json
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    out = json.loads(export_json(rows, ["id", "name"]))
    assert out == rows


def test_export_json_empty():
    assert export_json([]) == "[]"


def test_export_insert_escapes_and_types():
    rows = [{"id": 1, "name": "O'Brien", "amt": 3.5, "flag": None}]
    sql = export_insert(rows, ["id", "name", "amt", "flag"], table="users")
    assert sql == 'INSERT INTO "users" ("id", "name", "amt", "flag") VALUES (1, \'O\'\'Brien\', 3.5, NULL);'


def test_export_insert_decimal_unquoted():
    """Decimal (NUMERIC/DECIMAL driver type) must be emitted as an unquoted number,
    not a quoted string literal; non-finite floats fall back to NULL."""
    from decimal import Decimal

    rows = [{"id": 1, "price": Decimal("19.99"), "bad": float("inf")}]
    sql = export_insert(rows, ["id", "price", "bad"], table="t")
    assert sql == 'INSERT INTO "t" ("id", "price", "bad") VALUES (1, 19.99, NULL);'


def test_export_insert_non_finite_decimal_is_null():
    """A non-finite Decimal (e.g. PostgreSQL NUMERIC 'NaN'/'Infinity') has no valid
    SQL numeric literal — it must become NULL, not a bare NaN/Infinity token."""
    from decimal import Decimal

    rows = [{"a": Decimal("NaN"), "b": Decimal("-Infinity"), "c": Decimal("9.99")}]
    sql = export_insert(rows, ["a", "b", "c"], table="t")
    assert sql == 'INSERT INTO "t" ("a", "b", "c") VALUES (NULL, NULL, 9.99);'


def test_export_insert_backslash_is_dialect_aware():
    """Backslash escaping must match each dialect's string rules: MySQL/MariaDB
    double it (backslash is an escape); PostgreSQL uses an E'' string with a doubled
    backslash (unambiguous regardless of standard_conforming_strings); SQLite and
    generic keep a single literal backslash (doubling would corrupt the value)."""
    rows = [{"path": "C:\\"}]  # one literal backslash
    cases = {
        "mysql": "'C:\\\\'",
        "mariadb": "'C:\\\\'",
        "postgres": "E'C:\\\\'",
        "sqlite": "'C:\\'",
        "generic": "'C:\\'",
    }
    for dialect, literal in cases.items():
        sql = export_insert(rows, ["path"], table="t", dialect=dialect)
        assert sql.endswith(literal + ");"), f"{dialect}: {sql!r} (want …{literal});)"


def test_export_csv_null_vs_empty():
    """NULL and empty string must be distinguishable in CSV output."""
    from dbaide.rendering.table import export_csv
    rows = [{"a": None, "b": ""}, {"a": "x", "b": "y"}]
    csv_text = export_csv(rows, ["a", "b"])
    lines = csv_text.strip().splitlines()
    assert lines[1] == "NULL,"  # NULL rendered as literal, empty string as empty


def test_export_csv_neutralizes_formula_injection():
    """A DB value beginning with a formula char (= + - @ \\t \\r) must be quoted with a
    leading apostrophe so spreadsheet apps treat it as text, not a formula — but plain
    numbers (incl. negatives) must stay intact."""
    import csv as _csv
    import io as _io
    from dbaide.rendering.table import export_csv

    rows = [
        {"v": "=HYPERLINK(\"http://evil\")"},
        {"v": "+1+2"},
        {"v": "@SUM(A1)"},
        {"v": "\tcmd"},
        {"v": "-5"},          # numeric string → preserved
        {"v": -42},           # int → preserved
        {"v": "-3.14"},       # numeric string → preserved
        {"v": "foo=bar"},     # '=' not leading → preserved
    ]
    csv_text = export_csv(rows, ["v"])
    parsed = [r[0] for r in _csv.reader(_io.StringIO(csv_text))][1:]  # skip header
    assert parsed[0].startswith("'=")
    assert parsed[1] == "'+1+2"
    assert parsed[2] == "'@SUM(A1)"
    assert parsed[3] == "'\tcmd"
    assert parsed[4] == "-5"
    assert parsed[5] == "-42"
    assert parsed[6] == "-3.14"
    assert parsed[7] == "foo=bar"
    # No raw cell may begin with a formula trigger.
    for cell in parsed:
        assert not (cell and cell[0] in "=+-@" and not cell.lstrip("+-").replace(".", "").isdigit())


def test_export_insert_empty():
    assert export_insert([]) == ""


def _structural_pipes(line: str) -> int:
    """Count unescaped '|' (the column separators), ignoring '\\|' inside cells."""
    n, i = 0, 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "|":
            n += 1
        i += 1
    return n


def test_export_markdown_escapes_pipe_and_newline():
    """A cell containing '|' or a newline must not break the table: '|' is escaped and
    newlines collapse to <br>, so every data row keeps exactly one cell per column."""
    rows = [{"id": 1, "name": "a|b"}, {"id": 2, "name": "x\ny"}, {"id": 3, "name": None}]
    md = export_markdown_table(rows, ["id", "name"])
    lines = md.splitlines()
    # header + separator + 3 data rows, each with 3 structural pipes (2 columns).
    assert len(lines) == 5
    for line in lines:
        assert _structural_pipes(line) == 3
    assert "a\\|b" in md          # pipe escaped, not a column break
    assert "x<br>y" in md         # newline collapsed
    assert "| NULL |" in md       # None rendered, structure intact


def test_sanitize_unquoted_event_handlers():
    """Unquoted HTML event handlers must also be stripped."""
    from dbaide.rendering.sanitize import sanitize_markdown_html
    assert "onerror" not in sanitize_markdown_html('<img src=x onerror=alert(1)>')
    assert "onload" not in sanitize_markdown_html('<svg onload=alert(1)>')
    assert "onclick" not in sanitize_markdown_html('<a onclick="alert(1)">x</a>')
    assert "onmouseover" not in sanitize_markdown_html("<div onmouseover='alert(1)'>x</div>")


def test_md_escape_cell_handles_pipes_and_newlines():
    from dbaide.rendering.table import md_escape_cell

    assert md_escape_cell("a | b") == "a \\| b"
    assert md_escape_cell("line1\nline2") == "line1<br>line2"
    assert md_escape_cell("c:\\path") == "c:\\\\path"
    # private alias kept for backwards compatibility
    from dbaide.rendering.table import _md_escape
    assert _md_escape is md_escape_cell


def test_developer_tools_markdown_escapes_multiline_comment():
    """A column comment with a newline/pipe must not break the schema-doc Markdown
    table (each data row must keep exactly 6 cells → 7 pipes)."""
    from dbaide.tools.dev import DeveloperTools

    class _StubStore:
        def instance_doc(self, instance): return None
        def database_docs(self, instance): return [{"name": "main", "description": ""}]
        def table_docs(self, instance, db): return [{"name": "t", "description": ""}]
        def column_docs(self, instance, db, table):
            return [{"name": "note", "data_type": "text",
                     "source_comment": "first line\nsecond | piped line"}]
        def database_dir(self, instance, db):
            import pathlib
            return pathlib.Path("/nonexistent")
        def read_json(self, path): return None

    md = DeveloperTools(_StubStore()).markdown("ci")
    data_rows = [ln for ln in md.splitlines() if ln.startswith("| note ")]
    assert data_rows, md
    import re
    structural = re.findall(r"(?<!\\)\|", data_rows[0])  # unescaped pipes = cell borders
    assert len(structural) == 7                 # 6 cells, the comment's '|' is escaped
    assert "<br>" in data_rows[0]               # newline collapsed, row intact


def test_format_result_text_keeps_rows_single_line():
    """A multi-line cell value must not break the fixed-width text table — every data
    row stays on one physical line so columns remain aligned."""
    from dbaide.rendering.table import format_result_text

    rows = [{"id": 1, "note": "line1\nline2\twith tab"}, {"id": 2, "note": "plain"}]
    out = format_result_text(rows, ["id", "note"])
    # header + separator + 2 data rows = 4 lines (the newline cell adds no extra line).
    assert len(out.splitlines()) == 4
    assert "line1 line2 with tab" in out          # newline/tab collapsed to spaces
